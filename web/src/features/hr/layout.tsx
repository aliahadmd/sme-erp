import { SectionIndex, SectionLayout } from "@/app/section-layout"
import { HR_TABS } from "@/app/section-tabs"
import { EmployeesPage } from "@/features/hr/hr-page"

export function HRIndex() {
  return (
    <SectionIndex tabs={HR_TABS}>
      <EmployeesPage />
    </SectionIndex>
  )
}

export function HRLayout() {
  return <SectionLayout tabs={HR_TABS} />
}
