from erp_cf.invoices import Invoices
from erp_cf.payments import Payments


def invoice(action):
    async def run(plan, actor, payload):
        return await getattr(Invoices(plan), action)(payload)

    return run


def payment(action):
    async def run(plan, actor, payload):
        return await getattr(Payments(plan), action)(payload)

    return run


COMMANDS = {
    "invoicing.invoice.create": ("invoicing.invoice.create", 201, invoice("create")),
    "invoicing.invoice.update": ("invoicing.invoice.update", 200, invoice("update")),
    "invoicing.invoice.delete": ("invoicing.invoice.update", 204, invoice("delete")),
    "invoicing.invoice.post": ("invoicing.invoice.post", 200, invoice("post")),
    "invoicing.invoice.void": ("invoicing.invoice.void", 200, invoice("void")),
    "invoicing.invoice.email": ("invoicing.invoice.post", 200, invoice("email")),
    "invoicing.payment.create": ("invoicing.payment.create", 201, payment("create")),
    "invoicing.payment.allocate": ("invoicing.payment.create", 200, payment("allocate_existing")),
    "invoicing.payment.void": ("invoicing.payment.void", 200, payment("void")),
}
