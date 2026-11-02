import useAuth from "@/hooks/useAuth"

const UserInformation = () => {
  const { user } = useAuth()
  return (
    <section className="max-w-md space-y-4 py-4">
      <h2 className="text-lg font-semibold">个人信息</h2>
      <p className="text-sm text-muted-foreground">
        姓名和邮箱由企业管理员按员工名单维护。
      </p>
      <dl className="space-y-4">
        {[
          ["姓名", user?.full_name],
          ["登录名", user?.username],
          ["邮箱", user?.email],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-sm text-muted-foreground">{label}</dt>
            <dd className="py-2 break-all">{value || "未填写"}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

export default UserInformation
