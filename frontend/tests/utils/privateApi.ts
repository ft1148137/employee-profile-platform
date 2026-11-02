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
  mustChangePassword = false,
}: {
  email: string
  password: string
  mustChangePassword?: boolean
}) => {
  const response = await PrivateService.createUser({
    body: {
      username: email.split("@")[0],
      email,
      password,
      full_name: "Test User",
      must_change_password: mustChangePassword,
    },
  })
  return response.data
}
