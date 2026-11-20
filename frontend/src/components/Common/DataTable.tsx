import {
  type ColumnDef,
  createPaginatedRowModel,
  flexRender,
  type OnChangeFn,
  type PaginationState,
  type RowData,
  rowPaginationFeature,
  tableFeatures,
  useTable,
} from "@tanstack/react-table"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const dataTableFeatures = tableFeatures({
  rowPaginationFeature,
  paginatedRowModel: createPaginatedRowModel(),
})

export type DataTableFeatures = typeof dataTableFeatures

interface DataTableProps<TData extends RowData> {
  columns: ColumnDef<DataTableFeatures, TData>[]
  data: TData[]
  pagination?: PaginationState
  onPaginationChange?: OnChangeFn<PaginationState>
  pageSizeOptions?: number[]
  rowCount?: number
  getRowId?: (row: TData) => string
  onRowClick?: (row: TData) => void
}

export function DataTable<TData extends RowData>({
  columns,
  data,
  pagination,
  onPaginationChange,
  rowCount,
  getRowId,
  pageSizeOptions = [5, 10, 25, 50],
  onRowClick,
}: DataTableProps<TData>) {
  const table = useTable({
    features: dataTableFeatures,
    data,
    columns,
    getRowId,
    ...(pagination
      ? {
          state: { pagination },
          onPaginationChange,
          manualPagination: true,
          rowCount,
        }
      : {}),
  })

  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-surface-border bg-surface [&>[data-slot=table-container]]:rounded-none [&>[data-slot=table-container]]:border-0">
      <Table>
        <TableHeader className="bg-surface-muted">
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => {
                return (
                  <TableHead
                    key={header.id}
                    className="h-10 px-3 normal-case tracking-normal"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                className={
                  onRowClick
                    ? "cursor-pointer focus-visible:outline focus-visible:outline-ring"
                    : undefined
                }
                tabIndex={onRowClick ? 0 : undefined}
                onClick={
                  onRowClick
                    ? (event) => {
                        if (
                          !(
                            event.target instanceof Element &&
                            event.target.closest("a,button")
                          )
                        )
                          onRowClick(row.original)
                      }
                    : undefined
                }
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (
                          event.target === event.currentTarget &&
                          event.key === "Enter"
                        )
                          onRowClick(row.original)
                      }
                    : undefined
                }
              >
                {row.getAllCells().map((cell) => (
                  <TableCell key={cell.id} className="h-[43px] px-3 py-0.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={columns.length}
                className="h-32 text-center text-muted-foreground"
              >
                暂无数据
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      {(table.getPageCount() > 1 || (pagination && (rowCount ?? 0) > 0)) && (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-surface-border border-t bg-surface-muted px-3 py-1.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <div className="text-xs text-muted-foreground">
              第{" "}
              {table.state.pagination.pageIndex *
                table.state.pagination.pageSize +
                1}
              –
              {Math.min(
                (table.state.pagination.pageIndex + 1) *
                  table.state.pagination.pageSize,
                rowCount ?? data.length,
              )}{" "}
              条，共{" "}
              <span className="font-medium text-foreground">
                {rowCount ?? data.length}
              </span>{" "}
              条
            </div>
            <div className="flex items-center gap-x-2">
              <p className="text-xs text-muted-foreground">每页条数</p>
              <Select
                value={`${table.state.pagination.pageSize}`}
                onValueChange={(value) => {
                  table.setPageSize(Number(value))
                }}
              >
                <SelectTrigger
                  aria-label="每页条数"
                  size="sm"
                  className="w-16 bg-surface"
                >
                  <SelectValue placeholder={table.state.pagination.pageSize} />
                </SelectTrigger>
                <SelectContent side="top">
                  {pageSizeOptions.map((pageSize) => (
                    <SelectItem key={pageSize} value={`${pageSize}`}>
                      {pageSize}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex items-center gap-x-3">
            <div className="flex items-center gap-x-1 text-xs text-muted-foreground">
              <span>页码</span>
              <span className="font-medium text-foreground">
                {table.state.pagination.pageIndex + 1}
              </span>
              <span>/</span>
              <span className="font-medium text-foreground">
                {table.getPageCount()}
              </span>
            </div>

            <div className="flex items-center gap-x-1">
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => table.setPageIndex(0)}
                disabled={!table.getCanPreviousPage()}
              >
                <span className="sr-only">第一页</span>
                <ChevronsLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
              >
                <span className="sr-only">上一页</span>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
              >
                <span className="sr-only">下一页</span>
                <ChevronRight className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => table.setPageIndex(table.getPageCount() - 1)}
                disabled={!table.getCanNextPage()}
              >
                <span className="sr-only">最后一页</span>
                <ChevronsRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
