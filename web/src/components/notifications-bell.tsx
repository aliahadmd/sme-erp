import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bell } from "lucide-react"

import { reportsApi } from "@/features/reports/api"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { queryKeys } from "@/lib/query-keys"

export function NotificationsBell() {
  const queryClient = useQueryClient()
  const { data: countData } = useQuery({
    queryKey: queryKeys.notifications(),
    queryFn: () => reportsApi.unreadCount(),
    refetchInterval: 30_000,
  })
  const { data } = useQuery({
    queryKey: [...queryKeys.notifications(), "list"],
    queryFn: () => reportsApi.notifications(),
  })

  const markRead = useMutation({
    mutationFn: (id: string) => reportsApi.markRead(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.notifications() }),
  })

  const unread = countData?.count ?? 0
  const items = data?.items ?? []

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon" aria-label="Notifications" />}
      >
        <span className="relative">
          <Bell className="size-4" />
          {unread > 0 && (
            <span className="absolute -top-1.5 -right-1.5 flex size-4 items-center justify-center rounded-full bg-destructive text-[10px] font-medium text-destructive-foreground">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <div className="border-b px-3 py-2 text-sm font-semibold">Notifications</div>
        <div className="max-h-72 overflow-y-auto">
          {items.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              Nothing yet — events will land here
            </p>
          )}
          {items.map((notification) => (
            <button
              key={notification.id}
              type="button"
              className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-muted"
              onClick={() => !notification.read_at && markRead.mutate(notification.id)}
            >
              <span className={notification.read_at ? "text-muted-foreground" : "font-medium"}>
                {notification.title}
              </span>
              {notification.body && (
                <span className="text-xs text-muted-foreground">{notification.body}</span>
              )}
              <span className="text-[10px] text-muted-foreground/70">
                {new Date(notification.created_at).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
