import { useNavigate, useRouter } from '@tanstack/react-router'
import { cn } from '@/lib/utils'
import { Main } from '@/components/layout/main'
import { Button } from '@/components/ui/button'

type GeneralErrorProps = React.HTMLAttributes<HTMLDivElement> & {
  minimal?: boolean
}

export function GeneralError({
  className,
  minimal = false,
}: GeneralErrorProps) {
  const navigate = useNavigate()
  const { history } = useRouter()
  return (
    <div className={cn('h-svh w-full', className)}>
      <div className='m-auto flex h-full w-full flex-col items-center justify-center gap-2'>
        {!minimal && (
          <h1 className='text-[7rem] leading-tight font-bold'>500</h1>
        )}
        <span className='font-medium'>Something went wrong</span>
        <p className='text-muted-foreground text-center'>
          The route could not render.
        </p>
        {!minimal && (
          <div className='mt-6 flex gap-4'>
            <Button variant='outline' onClick={() => history.go(-1)}>
              Go Back
            </Button>
            <Button onClick={() => navigate({ to: '/' })}>Back to Home</Button>
          </div>
        )}
      </div>
    </div>
  )
}

export function RouteErrorBoundary() {
  return (
    <Main>
      <div
        role='alert'
        className='border-border bg-card text-card-foreground flex min-h-64 flex-col items-center justify-center rounded-lg border p-6 text-center'
      >
        <span className='font-medium'>Something went wrong</span>
        <p className='text-muted-foreground mt-1 text-sm'>
          The route could not render.
        </p>
      </div>
    </Main>
  )
}
