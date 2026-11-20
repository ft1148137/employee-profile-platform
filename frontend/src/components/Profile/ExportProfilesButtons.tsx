import { useMutation } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { PeopleSearchService } from "@/client"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function ExportProfilesButtons({ orgId }: { orgId: string }) {
  const download = useMutation({
    mutationFn: async (format: "csv" | "json") => {
      const { data } = await PeopleSearchService.searchExportProfiles({
        path: { org_id: orgId },
        query: { format },
        responseType: "blob",
      })
      if (!(data instanceof Blob)) throw new Error("画像导出失败")
      const url = URL.createObjectURL(data)
      const link = document.createElement("a")
      link.href = url
      link.download = `已发布画像.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  return (
    <>
      <TopbarActions>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              aria-label="导出画像"
              disabled={download.isPending}
            >
              <Download aria-hidden="true" />
              <span className="hidden sm:inline">导出画像</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-60">
            <DropdownMenuLabel className="font-normal text-muted-foreground text-xs">
              导出可见范围内的全部已发布画像，不受筛选条件影响。
            </DropdownMenuLabel>
            {(["csv", "json"] as const).map((format) => (
              <DropdownMenuItem
                key={format}
                onSelect={() => download.mutate(format)}
              >
                导出画像（{format.toUpperCase()}）
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </TopbarActions>
      <SetupError error={download.error} />
    </>
  )
}
