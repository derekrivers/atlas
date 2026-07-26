import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { Badge } from '@/components/ui/badge'

type OperatorViewPlaceholderProps = {
  eyebrow: string
  title: string
  body: string
}

export function OperatorViewPlaceholder({
  eyebrow,
  title,
  body,
}: OperatorViewPlaceholderProps) {
  return (
    <>
      <Header fixed>
        <Search placeholder='Search routes' />
        <div className='ms-auto flex items-center gap-2'>
          <ThemeSwitch />
        </div>
      </Header>
      <Main>
        <div className='flex flex-col gap-6'>
          <div className='flex flex-col gap-3 border-b pb-6 sm:flex-row sm:items-end sm:justify-between'>
            <div className='space-y-1'>
              <p className='text-muted-foreground text-sm font-medium'>
                {eyebrow}
              </p>
              <h1 className='text-2xl font-semibold tracking-normal'>
                {title}
              </h1>
            </div>
            <Badge variant='outline' className='w-fit'>
              Placeholder
            </Badge>
          </div>
          <section className='grid gap-3 md:grid-cols-3'>
            <div className='border-border bg-card text-card-foreground rounded-lg border p-4'>
              <p className='text-sm font-medium'>Data Contract</p>
              <p className='text-muted-foreground mt-2 text-sm'>{body}</p>
            </div>
            <div className='border-border bg-card text-card-foreground rounded-lg border p-4'>
              <p className='text-sm font-medium'>Phase Boundary</p>
              <p className='text-muted-foreground mt-2 text-sm'>
                This scaffold performs no reads from the Atlas API.
              </p>
            </div>
            <div className='border-border bg-card text-card-foreground rounded-lg border p-4'>
              <p className='text-sm font-medium'>Surface State</p>
              <p className='text-muted-foreground mt-2 text-sm'>
                Shell chrome, navigation, theme, and route fallback behavior are
                active.
              </p>
            </div>
          </section>
        </div>
      </Main>
    </>
  )
}
