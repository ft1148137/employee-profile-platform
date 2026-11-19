import { useMutation } from "@tanstack/react-query"
import { PeopleSearchService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

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
    <section aria-label="导出已发布画像" className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {(["csv", "json"] as const).map((format) => (
          <Button
            key={format}
            variant="outline"
            disabled={download.isPending}
            onClick={() => download.mutate(format)}
          >
            导出已发布画像（{format.toUpperCase()}）
          </Button>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        导出本人可见的全部已发布判断，不受页面筛选影响。
      </p>
      <SetupError error={download.error} />
    </section>
  )
}
