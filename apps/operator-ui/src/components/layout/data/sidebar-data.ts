import {
  BookOpen,
  GitBranch,
  LayoutDashboard,
  Network,
  ListChecks,
  Ticket,
  TicketCheck,
} from 'lucide-react'
import { operatorSurfaces, type OperatorSurfaceId } from '@/app-shell/surfaces'
import { type SidebarData } from '../types'

const surfaceIcons = {
  overview: LayoutDashboard,
  tickets: Ticket,
  'ticket-detail': TicketCheck,
  reviews: ListChecks,
  'critical-path': GitBranch,
  'dependency-graph': Network,
  lessons: BookOpen,
} satisfies Record<OperatorSurfaceId, React.ElementType>

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: 'Operate',
      items: operatorSurfaces.map((surface) => ({
        title: surface.title,
        url: surface.href,
        icon: surfaceIcons[surface.id],
      })),
    },
  ],
}
