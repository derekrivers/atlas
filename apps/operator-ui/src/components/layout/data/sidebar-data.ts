import {
  BookOpen,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Ticket,
} from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: 'Operate',
      items: [
        {
          title: 'Overview',
          url: '/',
          icon: LayoutDashboard,
        },
        {
          title: 'Tickets',
          url: '/tickets',
          icon: Ticket,
        },
        {
          title: 'Review Queue',
          url: '/reviews',
          icon: ListChecks,
        },
        {
          title: 'Critical Path',
          url: '/critical-path',
          icon: GitBranch,
        },
        {
          title: 'Lessons',
          url: '/lessons',
          icon: BookOpen,
        },
      ],
    },
  ],
}
