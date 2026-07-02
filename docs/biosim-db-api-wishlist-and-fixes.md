# Fixes
- ### Issue:
    - Pagination is broken on the `/projects/summary_filtered` endpoint; too many results are returned once past the first page

___

# Wishlist
- ### Feature: Optimized & Decoupled Endpoints
    - Split the existing endpoint into two GET requests; one to handle results, the other to handle tags & categories
      - In the result-fetching endpoint, make results return a slim & optimized model as follows:
        ```ts
            export interface ProjectStub {
                id: number
                simulationRun: string
                created: string
                updated: string
                name: string
                summary: string
                model_format: string
                image_url: string | undefined
            }
        ```
        - This endpoint should accept the following queryParams: `page (number), perPage (number), filters (valueFrequency[]), searchTerm (string)`
      - In the category & tag fetching endpoints, return the current data model as the old API:
        ```ts
          export interface ValueFrequency {
              value: string,
              count: number
          }

          export interface ProjectQueryStat {
              target: string,
              valueFrequencies: ValueFrequency[]
          }
          ```
        - If this endpoint's results can vary depending on the filters, it should accept the following queryParams: `page (number), perPage (number), filters (valueFrequency[]), searchTerm (string)`
