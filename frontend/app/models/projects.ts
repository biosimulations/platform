export interface ProjectSearchFilter {
  target: string
  allowable_values: string[]
}

export interface ProjectSearchMenuItemValue {
  target: string
  value: string
}

export interface ValueFrequency {
  value: string,
  count: number
}

export interface ProjectQueryStat {
  target: string,
  valueFrequencies: ValueFrequency[]
}

export interface ProjectQueryStatFilterValue {
  value: string
  label: string
}

export interface ProjectQueryStatFilter {
  target: string
  values: ProjectQueryStatFilterValue[]
  _index: number
}

export interface Projects {
  projectSummaries: ProjectSummary[]
  totalMatchingProjectSummaries: number
  queryStats: ProjectQueryStat[]
}

// <editor-fold desc="For when viewing projects">
export interface ProjectSummary {
  id: number
  simulationRun: any
  created: string
  updated: string
  owner: any
}

export interface ProjectStub {
  id: string
  simulationRun: string
  created: string
  updated: string
  name: string
  summary: string
  model_format: string
  image_url: string | undefined
}

// Response of GET /projects (platform backend): a page of results + total count.
export interface ProjectStubPage {
  items: ProjectStub[]
  total: number
}
// </editor-fold>
