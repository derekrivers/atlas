import { useMemo, useState } from 'react'
import { Eye } from 'lucide-react'
import type { components } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import { useLessonsQuery } from '@/api/query-hooks'
import { Main } from '@/components/layout/main'
import { EmptyCollectionState, LoadingState, RequestErrorState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

type Schema = components['schemas']
type EntityStatus = Schema['EntityStatus']
type LessonItem = Schema['LessonItemSchema']

const DEFAULT_LESSON_STATUS: EntityStatus = 'draft'
const LESSON_STATUS_FACETS: readonly EntityStatus[] = atlasOpenApiEnums.EntityStatus
const EMPTY_LESSONS: readonly LessonItem[] = []

function formatEnumValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function formatConfidence(confidence: number | null): string {
  return confidence === null ? 'Unscored' : confidence.toFixed(2)
}

function formatCreator(lesson: LessonItem): string {
  return `${lesson.created_by_type} / ${lesson.created_by_id}`
}

function isEntityStatus(value: string): value is EntityStatus {
  return LESSON_STATUS_FACETS.includes(value as EntityStatus)
}

function EmptyLessonsState({
  selectedStatus,
  totalLessons,
}: {
  selectedStatus: EntityStatus
  totalLessons: number
}) {
  if (totalLessons === 0) {
    return (
      <EmptyCollectionState
        title='No lessons'
        detail='The Atlas API returned an empty lesson collection.'
      />
    )
  }

  return (
    <EmptyCollectionState
      title={`No ${selectedStatus} lessons`}
      detail='No lessons match the selected status facet.'
    />
  )
}

function LessonTags({ tags }: { tags: readonly string[] }) {
  if (tags.length === 0) {
    return <span className='text-muted-foreground'>None</span>
  }

  return (
    <div className='flex min-w-0 flex-wrap gap-1'>
      {tags.map((tag) => (
        <Badge key={tag} variant='outline'>
          {tag}
        </Badge>
      ))}
    </div>
  )
}

function LessonsTable({
  lessons,
  onSelectLesson,
}: {
  lessons: readonly LessonItem[]
  onSelectLesson: (lesson: LessonItem) => void
}) {
  return (
    <div className='border-border bg-card text-card-foreground overflow-hidden rounded-lg border'>
      <Table aria-label='Lessons' className='table-fixed'>
        <TableHeader>
          <TableRow>
            <TableHead className='w-[8%] whitespace-normal'>Category</TableHead>
            <TableHead className='w-[16%] whitespace-normal'>Title</TableHead>
            <TableHead className='w-[8%] whitespace-normal'>Status</TableHead>
            <TableHead className='w-[8%] whitespace-normal'>Confidence</TableHead>
            <TableHead className='w-[10%] whitespace-normal'>Tags</TableHead>
            <TableHead className='w-[12%] whitespace-normal'>Creator</TableHead>
            <TableHead className='w-[14%] whitespace-normal'>Source ticket ID</TableHead>
            <TableHead className='hidden w-[10%] whitespace-normal xl:table-cell'>
              Created
            </TableHead>
            <TableHead className='hidden w-[10%] whitespace-normal xl:table-cell'>
              Updated
            </TableHead>
            <TableHead className='w-12'>
              <span className='sr-only'>Details</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lessons.map((lesson) => (
            <TableRow key={lesson.id}>
              <TableCell className='whitespace-normal'>
                {formatEnumValue(lesson.category)}
              </TableCell>
              <TableCell className='max-w-md whitespace-normal'>
                <span className='font-medium'>{lesson.title}</span>
              </TableCell>
              <TableCell className='whitespace-normal'>
                <Badge variant='secondary'>
                  {formatEnumValue(lesson.status)}
                </Badge>
              </TableCell>
              <TableCell className='whitespace-normal'>
                {formatConfidence(lesson.confidence)}
              </TableCell>
              <TableCell className='whitespace-normal'>
                <LessonTags tags={lesson.tags} />
              </TableCell>
              <TableCell className='break-words whitespace-normal'>
                {formatCreator(lesson)}
              </TableCell>
              <TableCell className='whitespace-normal'>
                <code className='bg-muted text-foreground rounded px-1 py-0.5 text-xs break-all'>
                  {lesson.source_ticket_id}
                </code>
              </TableCell>
              <TableCell className='hidden break-all whitespace-normal xl:table-cell'>
                {lesson.created_at}
              </TableCell>
              <TableCell className='hidden break-all whitespace-normal xl:table-cell'>
                {lesson.updated_at}
              </TableCell>
              <TableCell>
                <Button
                  type='button'
                  variant='ghost'
                  size='icon'
                  aria-label={`View lesson details: ${lesson.title}`}
                  onClick={() => onSelectLesson(lesson)}
                >
                  <Eye aria-hidden='true' className='size-4' />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function LiteralUuidList({
  emptyLabel = 'None',
  values,
}: {
  emptyLabel?: string
  values: readonly string[]
}) {
  if (values.length === 0) {
    return <span className='text-muted-foreground'>{emptyLabel}</span>
  }

  return (
    <ul className='space-y-1'>
      {values.map((value) => (
        <li key={value}>
          <code className='bg-muted text-foreground rounded px-1 py-0.5 text-xs break-all'>
            {value}
          </code>
        </li>
      ))}
    </ul>
  )
}

function DrawerSection({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <section className='space-y-2'>
      <h3 className='text-sm font-medium'>{label}</h3>
      <p className='text-muted-foreground whitespace-pre-wrap text-sm leading-6'>
        {value}
      </p>
    </section>
  )
}

function LessonDetailDrawer({
  lesson,
  onOpenChange,
}: {
  lesson: LessonItem | undefined
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={lesson !== undefined} onOpenChange={onOpenChange}>
      {lesson ? (
        <SheetContent className='w-full overflow-hidden p-0 sm:max-w-2xl'>
          <SheetHeader className='border-b p-6 pe-12'>
            <div className='flex flex-wrap items-center gap-2'>
              <Badge variant='secondary'>{formatEnumValue(lesson.status)}</Badge>
              <Badge variant='outline'>{formatEnumValue(lesson.category)}</Badge>
            </div>
            <SheetTitle className='text-xl tracking-normal'>
              {lesson.title}
            </SheetTitle>
            <SheetDescription>
              {formatCreator(lesson)} / {lesson.created_at}
            </SheetDescription>
          </SheetHeader>
          <ScrollArea className='min-h-0 flex-1'>
            <div className='space-y-6 p-6'>
              <DrawerSection label='Problem' value={lesson.problem} />
              <DrawerSection label='Solution' value={lesson.solution} />
              <DrawerSection label='Outcome' value={lesson.outcome} />
              <Separator />
              <section className='grid gap-4 text-sm sm:grid-cols-2'>
                <div className='space-y-1'>
                  <h3 className='font-medium'>Confidence</h3>
                  <p className='text-muted-foreground'>
                    {formatConfidence(lesson.confidence)}
                  </p>
                </div>
                <div className='space-y-1'>
                  <h3 className='font-medium'>Updated</h3>
                  <p className='text-muted-foreground break-all'>
                    {lesson.updated_at}
                  </p>
                </div>
                <div className='space-y-1 sm:col-span-2'>
                  <h3 className='font-medium'>Tags</h3>
                  <LessonTags tags={lesson.tags} />
                </div>
                <div className='space-y-1 sm:col-span-2'>
                  <h3 className='font-medium'>Source ticket ID</h3>
                  <code className='bg-muted text-foreground rounded px-1 py-0.5 text-xs break-all'>
                    {lesson.source_ticket_id}
                  </code>
                </div>
                <div className='space-y-1 sm:col-span-2'>
                  <h3 className='font-medium'>Related ticket IDs</h3>
                  <LiteralUuidList values={lesson.related_ticket_ids} />
                </div>
              </section>
            </div>
          </ScrollArea>
        </SheetContent>
      ) : null}
    </Sheet>
  )
}

function lessonCountForStatus(
  lessons: readonly LessonItem[],
  status: EntityStatus
): number {
  return lessons.filter((lesson) => lesson.status === status).length
}

export function LessonsView() {
  const lessonsQuery = useLessonsQuery()
  const [selectedStatus, setSelectedStatus] = useState<EntityStatus>(
    DEFAULT_LESSON_STATUS
  )
  const [selectedLesson, setSelectedLesson] = useState<LessonItem | undefined>()
  const lessons = lessonsQuery.data?.lessons ?? EMPTY_LESSONS
  const filteredLessons = useMemo(
    () => lessons.filter((lesson) => lesson.status === selectedStatus),
    [lessons, selectedStatus]
  )

  if (lessonsQuery.isLoading) {
    return (
      <Main>
        <LoadingState label='Loading lessons' />
      </Main>
    )
  }

  if (lessonsQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={lessonsQuery.error}
          title='Lessons request failed'
        />
      </Main>
    )
  }

  return (
    <Main fluid>
      <div className='flex flex-col gap-6'>
        <div className='flex flex-col gap-3 border-b pb-6 sm:flex-row sm:items-end sm:justify-between'>
          <div className='space-y-1'>
            <p className='text-muted-foreground text-sm font-medium'>
              Knowledge
            </p>
            <h1 className='text-2xl font-semibold tracking-normal'>Lessons</h1>
          </div>
          <Badge variant='outline' className='w-fit'>
            {filteredLessons.length} of {lessons.length}
          </Badge>
        </div>

        <Tabs
          value={selectedStatus}
          onValueChange={(value) => {
            if (isEntityStatus(value)) {
              setSelectedStatus(value)
            }
          }}
        >
          <TabsList
            aria-label='Lesson status facets'
            className='h-auto flex-wrap justify-start'
          >
            {LESSON_STATUS_FACETS.map((status) => (
              <TabsTrigger key={status} value={status}>
                {formatEnumValue(status)}
                <Badge variant='secondary'>
                  {lessonCountForStatus(lessons, status)}
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
          {LESSON_STATUS_FACETS.map((status) => {
            const statusLessons = lessons.filter(
              (lesson) => lesson.status === status
            )

            return (
              <TabsContent
                key={status}
                value={status}
                forceMount
                className='mt-4 data-[state=inactive]:hidden'
              >
                {status === selectedStatus ? (
                  statusLessons.length === 0 ? (
                    <EmptyLessonsState
                      selectedStatus={status}
                      totalLessons={lessons.length}
                    />
                  ) : (
                    <LessonsTable
                      lessons={statusLessons}
                      onSelectLesson={setSelectedLesson}
                    />
                  )
                ) : null}
              </TabsContent>
            )
          })}
        </Tabs>
      </div>
      <LessonDetailDrawer
        lesson={selectedLesson}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedLesson(undefined)
          }
        }}
      />
    </Main>
  )
}
