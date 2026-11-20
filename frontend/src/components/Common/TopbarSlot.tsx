import {
  type ReactNode,
  type ReactPortal,
  useLayoutEffect,
  useState,
} from "react"
import { createPortal } from "react-dom"

export const TOPBAR_ACTIONS_ID = "workspace-topbar-actions"

export function TopbarActions({
  children,
}: {
  children: ReactNode
}): ReactPortal | null {
  const [target, setTarget] = useState<HTMLElement | null>(null)

  useLayoutEffect(() => {
    setTarget(document.getElementById(TOPBAR_ACTIONS_ID))
  }, [])

  return target ? createPortal(children, target) : null
}
