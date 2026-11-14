export function ProfileVisibility() {
  return (
    <details className="rounded-lg border p-3 text-sm text-muted-foreground">
      <summary className="cursor-pointer font-medium">谁能看到</summary>
      <p className="mt-2">
        正式画像（能力、等级、状态、陈述）本人、管辖经理、企业管理员可见；依据说明和受限自述本人和管辖经理可见；草稿只有本人可见。
      </p>
      <p>查看不通知本人、不留记录；直接修改发布后通知本人。</p>
      <p>
        离职后画像和自述归档只读，仅企业管理员可见归档的能力、等级和状态；受限自述和经理评价正文不显示，管理员兼任部门经理也不可见。账号停用，未处理的请求自动撤回；恢复在职后按在职规则可见，请求不会自动恢复。
      </p>
      <p>
        数据保存在本企业自己部署的服务器上；存放位置、备份和保留期限请问企业管理员。
      </p>
    </details>
  )
}
