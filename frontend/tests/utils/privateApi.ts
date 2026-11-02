// Note: the `PrivateService` is only available when generating the client
// for local environments
import { PrivateService } from "../../src/client"
import { client } from "../../src/client/client.gen"

client.setConfig({
  baseURL: `${process.env.VITE_API_URL}`,
})

export const createUser = async ({
  email,
  password,
  withEmptyProfile = false,
}: {
  email: string
  password: string
  withEmptyProfile?: boolean
}) => {
  const response = await PrivateService.createUser({
    query: { with_empty_profile: withEmptyProfile },
    body: {
      username: email.split("@")[0],
      email,
      password,
      full_name: "测试员工",
    },
  })
  return response.data
}
