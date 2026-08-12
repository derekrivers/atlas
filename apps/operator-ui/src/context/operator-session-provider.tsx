import {
  createContext,
  type FormEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  atlasForgetSession,
  atlasLogin,
  atlasLogout,
  AtlasRequestError,
} from '@/api/client'
import { atlasQueryKeys, useSessionQuery } from '@/api/query-hooks'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type SessionFlowReason = 'expired' | 'refresh' | 'sign-in'

type OperatorSessionContextValue = {
  authenticated: boolean
  beginSessionFlow: (reason?: SessionFlowReason) => void
  endSession: () => Promise<void>
  expireSession: () => Promise<void>
  expiresAt: string | null
  lessonDecisionRevision: number
}

const OperatorSessionContext = createContext<OperatorSessionContextValue | null>(
  null
)

function loginErrorMessage(error: unknown): string {
  if (error instanceof AtlasRequestError && error.status === 401) {
    return 'The bootstrap token was refused. Enter the current runtime token.'
  }
  if (error instanceof AtlasRequestError && error.status === 429) {
    return 'Sign-in is temporarily throttled. Wait before trying again.'
  }
  return 'The operator session could not be created. Check that the writable loopback API is running.'
}

function flowCopy(reason: SessionFlowReason): {
  description: string
  title: string
} {
  if (reason === 'expired') {
    return {
      description:
        'Your operator session expired. Sign in again, then re-review the lesson before submitting another ruling.',
      title: 'Session expired',
    }
  }
  if (reason === 'refresh') {
    return {
      description:
        'This page was refreshed, so its in-memory write credential is gone. Sign in again to restore operator actions.',
      title: 'Restore operator session',
    }
  }
  return {
    description:
      'Enter the bootstrap token from the local Atlas runtime. It is submitted once to the session endpoint and is not remembered by this application.',
    title: 'Operator sign in',
  }
}

export function OperatorSessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const sessionQuery = useSessionQuery()
  const [authenticated, setAuthenticated] = useState(false)
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [lessonDecisionRevision, setLessonDecisionRevision] = useState(0)
  const [flowOpen, setFlowOpen] = useState(false)
  const [flowReason, setFlowReason] = useState<SessionFlowReason>('sign-in')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const returnFocus = useRef<HTMLElement | null>(null)
  const tokenInput = useRef<HTMLInputElement | null>(null)

  const beginSessionFlow = useCallback(
    (reason?: SessionFlowReason) => {
      returnFocus.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null
      setLoginError(null)
      setFlowReason(
        reason ??
          (sessionQuery.data?.authenticated === true ? 'refresh' : 'sign-in')
      )
      setFlowOpen(true)
    },
    [sessionQuery.data?.authenticated]
  )

  const invalidateLessonDecisionLifecycle = useCallback(() => {
    setLessonDecisionRevision((revision) => revision + 1)
    void queryClient.resetQueries({
      exact: true,
      queryKey: atlasQueryKeys.lessons(),
    })
  }, [queryClient])

  const expireSession = useCallback(async () => {
    invalidateLessonDecisionLifecycle()
    atlasForgetSession()
    setAuthenticated(false)
    setExpiresAt(null)
    setFlowReason('expired')
    setLoginError(null)
    setFlowOpen(true)
    await queryClient.invalidateQueries({
      exact: true,
      queryKey: atlasQueryKeys.session(),
    })
  }, [invalidateLessonDecisionLifecycle, queryClient])

  const endSession = useCallback(async () => {
    invalidateLessonDecisionLifecycle()
    try {
      await atlasLogout()
    } finally {
      atlasForgetSession()
      setAuthenticated(false)
      setExpiresAt(null)
      queryClient.setQueryData(atlasQueryKeys.session(), {
        authenticated: false,
        expires_at: null,
      })
    }
  }, [invalidateLessonDecisionLifecycle, queryClient])

  useEffect(() => {
    if (!authenticated || !expiresAt) {
      return
    }

    const delay = Date.parse(expiresAt) - Date.now()
    if (delay <= 0) {
      void expireSession()
      return
    }

    const timeout = window.setTimeout(
      () => void expireSession(),
      Math.min(delay, 2_147_483_647)
    )
    return () => window.clearTimeout(timeout)
  }, [authenticated, expireSession, expiresAt])

  useEffect(() => {
    if (flowOpen && loginError && !submitting) {
      tokenInput.current?.focus()
    }
  }, [flowOpen, loginError, submitting])

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return
    }

    const form = event.currentTarget
    const formData = new FormData(form)
    const tokenValue = formData.get('bootstrap-token')
    form.reset()
    if (typeof tokenValue !== 'string' || tokenValue.length === 0) {
      setLoginError('Enter the bootstrap token.')
      return
    }

    setSubmitting(true)
    setLoginError(null)
    try {
      const response = await atlasLogin({ token: tokenValue })
      setAuthenticated(true)
      setExpiresAt(response.expires_at)
      queryClient.setQueryData(atlasQueryKeys.session(), {
        authenticated: true,
        expires_at: response.expires_at,
      })
      setFlowOpen(false)
    } catch (error) {
      atlasForgetSession()
      setAuthenticated(false)
      setExpiresAt(null)
      setLoginError(loginErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  const contextValue = useMemo<OperatorSessionContextValue>(
    () => ({
      authenticated,
      beginSessionFlow,
      endSession,
      expireSession,
      expiresAt,
      lessonDecisionRevision,
    }),
    [
      authenticated,
      beginSessionFlow,
      endSession,
      expireSession,
      expiresAt,
      lessonDecisionRevision,
    ]
  )
  const copy = flowCopy(flowReason)

  return (
    <OperatorSessionContext.Provider value={contextValue}>
      {children}
      <Dialog
        open={flowOpen}
        onOpenChange={(open) => {
          if (!submitting) {
            setFlowOpen(open)
          }
        }}
      >
        <DialogContent
          aria-busy={submitting}
          onCloseAutoFocus={(event) => {
            if (returnFocus.current?.isConnected) {
              event.preventDefault()
              returnFocus.current.focus()
            }
            returnFocus.current = null
          }}
        >
          <form className='grid gap-4' onSubmit={submitLogin}>
            <DialogHeader>
              <DialogTitle>{copy.title}</DialogTitle>
              <DialogDescription>{copy.description}</DialogDescription>
            </DialogHeader>
            <div className='grid gap-2'>
              <Label htmlFor='atlas-bootstrap-token'>Bootstrap token</Label>
              <Input
                ref={tokenInput}
                id='atlas-bootstrap-token'
                name='bootstrap-token'
                type='password'
                autoComplete='off'
                autoCapitalize='none'
                spellCheck={false}
                disabled={submitting}
                aria-describedby={loginError ? 'atlas-login-error' : undefined}
                aria-invalid={loginError ? true : undefined}
                required
              />
              {loginError ? (
                <p
                  id='atlas-login-error'
                  className='text-destructive text-sm'
                  role='alert'
                >
                  {loginError}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type='button'
                variant='outline'
                disabled={submitting}
                onClick={() => setFlowOpen(false)}
              >
                Continue read-only
              </Button>
              <Button type='submit' disabled={submitting}>
                {submitting ? 'Signing in…' : 'Sign in'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </OperatorSessionContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useOperatorSession(): OperatorSessionContextValue {
  const value = useContext(OperatorSessionContext)
  if (!value) {
    throw new Error('useOperatorSession must be used inside OperatorSessionProvider')
  }
  return value
}

export function OperatorSessionControl() {
  const { authenticated, beginSessionFlow, endSession, expiresAt } =
    useOperatorSession()
  const [signingOut, setSigningOut] = useState(false)

  if (!authenticated) {
    return (
      <Button type='button' variant='outline' onClick={() => beginSessionFlow()}>
        Sign in
      </Button>
    )
  }

  return (
    <div className='flex items-center gap-2'>
      <span className='text-muted-foreground hidden text-xs lg:inline'>
        Session active{expiresAt ? ` until ${new Date(expiresAt).toLocaleTimeString()}` : ''}
      </span>
      <Button
        type='button'
        variant='outline'
        disabled={signingOut}
        onClick={async () => {
          setSigningOut(true)
          try {
            await endSession()
          } finally {
            setSigningOut(false)
          }
        }}
      >
        {signingOut ? 'Signing out…' : 'Sign out'}
      </Button>
    </div>
  )
}
