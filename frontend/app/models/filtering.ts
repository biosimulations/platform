export interface TableSort {
  id: string | undefined
  direction: 'asc' | 'desc' | undefined
}

export interface TableFilterConfig {
  filters: { [key: string]: TableFilter},
  _hidden_exist: boolean
}

export interface TableFilter {
  id?: string
  operator?: 'starts_with' | 'ends_with' | 'contains' | 'less_than' | 'equal' | 'greater_than' | 'before' | 'after' | 'on' | 'is_any'
  value?: any | any[]

  _filterType?: 'text' | 'date' | 'number' | 'boolean' | 'enum'
  _filterOptions?: any[]
}

export interface TablePagination {
  page: number
  perPage: number

  _total?: number
}
