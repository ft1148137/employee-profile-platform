import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { toast } from "sonner"

import {
  type Body_login_login_access_token as AccessToken,
  LoginService,
  type UserPublic,
  UsersService,
} from "@/client"
import { handleError, safeNext } from "@/utils"

const isLoggedIn = () => {
  return localStorage.getItem("access_token") !== null
}

const useAuth = (next?: string) => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: user } = useQuery<UserPublic | null, Error>({
    queryKey: ["currentUser"],
    queryFn: async () => (await UsersService.readUserMe()).data,
    enabled: isLoggedIn(),
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      body: data,
    })
    localStorage.setItem("access_token", response.data.access_token)
    queryClient.clear()
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      navigate({ href: safeNext(next) })
    },
    onError: handleError.bind((description: string) =>
      toast.error("登录失败", { description }),
    ),
  })

  const logout = () => {
    localStorage.removeItem("access_token")
    queryClient.clear()
    navigate({ to: "/login" })
  }

  return {
    loginMutation,
    logout,
    user,
  }
}

export { isLoggedIn }
export default useAuth
