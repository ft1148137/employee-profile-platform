import { useSyncExternalStore } from "react"

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback)
  return () => window.removeEventListener("storage", callback)
}

export function useCurrentConversation(key: string | undefined) {
  return useSyncExternalStore(subscribe, () =>
    key ? localStorage.getItem(key) : null,
  )
}

export function rememberConversation(key: string, id: string) {
  localStorage.setItem(key, id)
  window.dispatchEvent(new Event("storage"))
}
