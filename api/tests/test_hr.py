"""HR module tests — employees, leave requests, overlap/balance guards, RBAC."""


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def test_employee_and_department_flow(client):
    admin = await _admin(client)
    department = (
        await client.post("/api/hr/departments", headers=_auth(admin), json={"name": "Engineering"})
    ).json()
    employee = (
        await client.post(
            "/api/hr/employees",
            headers=_auth(admin),
            json={
                "full_name": "Grace Hopper",
                "department_id": department["id"],
                "position": "Engineer",
            },
        )
    ).json()
    assert employee["number"].startswith("EMP-")
    employees = (await client.get("/api/hr/employees", headers=_auth(admin))).json()
    assert any(e["id"] == employee["id"] for e in employees)


async def test_leave_overlap_and_balance_guards(client):
    admin = await _admin(client)
    department = (
        await client.post("/api/hr/departments", headers=_auth(admin), json={"name": "Leave Dept"})
    ).json()
    employee = (
        await client.post(
            "/api/hr/employees",
            headers=_auth(admin),
            json={
                "full_name": "Leave Taker",
                "department_id": department["id"],
            },
        )
    ).json()
    leave_type = (
        await client.post(
            "/api/hr/leave-types",
            headers=_auth(admin),
            json={"name": "Annual 10", "days_per_year": "10", "accrues": True},
        )
    ).json()

    # First request: 3 days, pending → approved
    r1 = (
        await client.post(
            "/api/hr/leave-requests",
            headers=_auth(admin),
            json={
                "employee_id": employee["id"],
                "type_id": leave_type["id"],
                "date_from": "2027-03-02",
                "date_to": "2027-03-04",
            },
        )
    ).json()
    assert r1["status"] == "pending"
    approved = (
        await client.post(f"/api/hr/leave-requests/{r1['id']}/approve", headers=_auth(admin))
    ).json()
    assert approved["status"] == "approved"

    # Overlapping request is rejected
    overlap = await client.post(
        "/api/hr/leave-requests",
        headers=_auth(admin),
        json={
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2027-03-03",
            "date_to": "2027-03-10",
        },
    )
    assert overlap.status_code == 409
    assert "Overlaps" in overlap.json()["error"]["detail"]

    # Exceeding the annual allowance is rejected
    over = await client.post(
        "/api/hr/leave-requests",
        headers=_auth(admin),
        json={
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2027-06-01",
            "date_to": "2027-06-30",
        },
    )
    assert over.status_code == 422
    assert "annual allowance" in over.json()["error"]["detail"]


async def test_hr_rbac_viewer_cannot_read_employees(client):
    admin = await _admin(client)
    await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "hrviewer@example.com",
            "password": "password123",
            "full_name": "HV",
            "role_codes": ["viewer"],
        },
    )
    viewer = (
        await client.post(
            "/api/auth/login",
            json={"email": "hrviewer@example.com", "password": "password123"},
        )
    ).json()["access_token"]
    response = await client.get("/api/hr/employees", headers=_auth(viewer))
    assert response.status_code == 403
