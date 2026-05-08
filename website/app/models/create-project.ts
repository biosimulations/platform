import type {Simulator} from "~/models/simulators";

export interface SimpleEnum {
  id: number
  label: string
}

export interface ModelingFramework {
  kisao_id: string
  name: string
  description: string
  simulation_types: SimpleEnum[]
}

export interface OntologyFetch {
  // Note: this interface doesn't need to exist if it only has one property. It's here for mental organization
  ontology: LocalOntologicalRoot // Do we need an array here? Are there distinct "categories" of sibling roots, or does one root ultimately own all paths?
}

export interface CreateProjectEnums {
  model_format: string // This is just to show to the user, nothing to do with it
  modeling_frameworks: ModelingFramework[]
  simulators: Simulator[] | undefined // Used in Step 2 if `author_intent` is 'runnable'
}

export interface ParseModelAPIResponse {
  enums: CreateProjectEnums
  file_id: string
}

export interface LocalOntologicalRoot {
  kisao_id: string
  name: string
  children: LocalOntologicalRoot[] | undefined // If undefined, this is a leaf.
  biosim_compatibility: 'full' | 'partial' | 'none' // Determines the levels of "smiley faces" (or other suitable iconography) to inform the user of their selection and BioSimulations parity. See comment below for more details:

  /**
   * From the perspective of a Root (`children` !== undefined)
   * 'Full' implies the root and all of its children are supported.
   * 'Partial' implies at least one child is supported, others are not.
   * 'None' means neither the root nor any of its children are supported.

   * From the perspective of a Leaf (`children` == undefined)
   * 'Full' implies that this particular algorithm is supported
   * 'Partial' is not a valid value for a leaf
   * 'None' implies that this particular algorithm is not supported

   * Question: Do we want to lazy-load the `children` property's items (i.e., defer fetching the children until a root is expanded), or is the ontology small enough to send all at once?
  */
}

export interface CreateProjectPayload {
  name: string // Step 1
  file_id: string // Step 1: UUID of uploaded/fetched file hash; set after ParseModelAPIResponse comes back
  author_intent: 'generic' | 'runnable' // Step 1: used to determine how to fetch ontology in step 2

  // ----- Hit API for following variables ----- //
  modeling_framework: string // Step 2: KiSAO ID?
  simulation_type: number // Step 2: SimpleEnum id? If `author_intent` is 'generic', hit ontology endpoint after selection change
  simulator: number | undefined // Step 2: if `author_intent` is 'runnable', show simulator selection dropdown and hit ontology endpoint after selection change

  // ----- Hit API (ontology endpoint) to receive OntologyFetch (under correct conditional circumstance outlined above) before `simulation_similarity` is settable
  simulation_similarity: 'exact' | 'equivalent' // Step 2: 'exact' when LocalOntologicalRoot's `children` property is undefined. Else, 'equivalent'

  initial_time: number // Step 3: advanced setting
  recording_start: number // Step 3: advanced setting
  end_time: number // Step 3
  num_steps: number // Step 3

  // ----- Hit API a second time for the following variables ----- //
  // Further discussion needed for tabular data modeling
}
