import type { FileSummary } from "@/client"

export function FileSummaryPreview({ preview }: { preview: FileSummary }) {
  const errors = preview.errors ?? []
  return (
    <section aria-label="文件预览" className="space-y-3">
      <h3 className="font-semibold">请核对导入预览</h3>
      <dl className="flex flex-wrap gap-3">
        {Object.entries(preview.counts).map(([label, count]) => (
          <div key={label} className="rounded-lg bg-muted px-3 py-2">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-lg font-semibold">{count}</dd>
          </div>
        ))}
      </dl>
      {errors.length ? (
        <div role="alert" className="overflow-x-auto text-destructive">
          <p>文件有错误，请修改后重新上传。</p>
          <table className="w-full text-left">
            <thead>
              <tr>
                <th className="p-2">位置</th>
                <th className="p-2">原因</th>
              </tr>
            </thead>
            <tbody>
              {errors.map((error, index) => (
                <tr key={index}>
                  <td className="p-2 whitespace-nowrap">
                    {error.location || "文件"}
                  </td>
                  <td className="p-2 break-words">{error.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-muted-foreground">
          尚未导入，请在下方确认卡中核对并确认应用。
        </p>
      )}
    </section>
  )
}
