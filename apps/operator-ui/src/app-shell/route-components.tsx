import { type OperatorSurface } from '@/app-shell/surfaces'
import { OperatorViewPlaceholder } from '@/features/placeholders/operator-view-placeholder'

export function PlaceholderRoute({ surface }: { surface: OperatorSurface }) {
  return (
    <OperatorViewPlaceholder
      eyebrow={surface.placeholder.eyebrow}
      title={surface.placeholder.title}
      body={surface.placeholder.body}
    />
  )
}

export function ThrowingOperatorView(): never {
  throw new Error('Intentional operator route failure')
}
