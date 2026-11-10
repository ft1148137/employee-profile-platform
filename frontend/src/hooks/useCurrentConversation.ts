import { useSyncExternalStore } from "react"

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback)
  return () => window.removeEventListener("storage", callback)
}

export function useCurrentConversation(key: string) {
  return useSyncExternalStore(subscribe, () => localStorage.getItem(key))
}

export function rememberConversation(key: string, id: string) {
  localStorage.setItem(key, id)
  window.dispatchEvent(new Event("storage"))
}
