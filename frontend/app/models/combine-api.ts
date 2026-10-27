/**
 * Types and interfaces for the COMBINE / BioSimulations validation and utility APIs.
 */

export type ValidationStatus = 'valid' | 'warnings' | 'invalid';

export interface ValidationMessage {
  _type?: 'ValidationMessage';
  summary: string;
  details?: ValidationMessage[];
}

export interface ValidationReport {
  _type?: 'ValidationReport';
  status: ValidationStatus;
  errors?: ValidationMessage[] | null;
  warnings?: ValidationMessage[] | null;
}

export type ModelLanguage = 'SBML'
  | 'CellML'
  | 'BNGL'
  | 'Smoldyn'
  | 'GINsim'
  | 'NeuroML'
  | 'LEMS'
  | 'RBA'
  | 'XPP';

export interface ModelLanguageOption {
  id: ModelLanguage;
  name: string;
  description: string;
  extensions: string[];
  accept: string;
  docsUrl?: string;
  sedUrn: string;
  omexManifestUri: string;
}

export interface ApiHttpError {
  status: number;
  title: string;
  detail?: string;
  type?: string;
}

export const MODEL_LANGUAGE_OPTIONS: ModelLanguageOption[] = [
  {
    id: 'SBML',
    name: 'SBML (Systems Biology Markup Language)',
    description: 'XML-based format for representing models of biological processes such as biochemical reaction networks.',
    extensions: ['.xml', '.sbml'],
    accept: '.xml,.sbml,text/xml,application/xml',
    docsUrl: 'https://sbml.org/',
    sedUrn: 'urn:sedml:language:sbml',
    omexManifestUri: 'http://identifiers.org/combine.specifications/sbml'
  },
  {
    id: 'CellML',
    name: 'CellML',
    description: 'XML-based format for describing mathematical models of cellular and subcellular processes.',
    extensions: ['.cellml', '.xml'],
    accept: '.cellml,.xml,text/xml,application/xml',
    docsUrl: 'https://www.cellml.org/',
    sedUrn: 'urn:sedml:language:cellml',
    omexManifestUri: 'http://identifiers.org/combine.specifications/cellml'
  },
  {
    id: 'BNGL',
    name: 'BNGL (BioNetGen Language)',
    description: 'Rule-based modeling language for specifying biochemical systems with combinatorial complexity.',
    extensions: ['.bngl'],
    accept: '.bngl,text/plain',
    docsUrl: 'https://bionetgen.org/',
    sedUrn: 'urn:sedml:language:bngl',
    omexManifestUri: 'http://identifiers.org/combine.specifications/bngl'
  },
  {
    id: 'Smoldyn',
    name: 'Smoldyn',
    description: 'Particle-based spatial stochastic simulator for biochemical and biophysical systems.',
    extensions: ['.smoldyn', '.txt'],
    accept: '.smoldyn,.txt,text/plain',
    docsUrl: 'https://www.smoldyn.org/',
    sedUrn: 'urn:sedml:language:smoldyn',
    omexManifestUri: 'http://identifiers.org/combine.specifications/smoldyn'
  },
  {
    id: 'GINsim',
    name: 'GINsim (GINML)',
    description: 'Qualitative simulation software for genetic and regulatory logical networks.',
    extensions: ['.ginml', '.zginml', '.xml'],
    accept: '.ginml,.zginml,.xml,text/xml,application/xml',
    docsUrl: 'http://ginsim.org/',
    sedUrn: 'urn:sedml:language:ginml',
    omexManifestUri: 'http://identifiers.org/combine.specifications/ginml'
  },
  {
    id: 'NeuroML',
    name: 'NeuroML',
    description: 'XML-based description language for computational neuroscience models.',
    extensions: ['.nml', '.xml'],
    accept: '.nml,.xml,text/xml,application/xml',
    docsUrl: 'https://docs.neuroml.org/',
    sedUrn: 'urn:sedml:language:neuroml',
    omexManifestUri: 'http://identifiers.org/combine.specifications/neuroml'
  },
  {
    id: 'LEMS',
    name: 'LEMS (Low Entropy Model Specification)',
    description: 'Language for expressing mathematical definitions and simulations of dynamic models.',
    extensions: ['.lems', '.xml'],
    accept: '.lems,.xml,text/xml,application/xml',
    docsUrl: 'https://lems.github.io/LEMS/',
    sedUrn: 'urn:sedml:language:lems',
    omexManifestUri: 'http://identifiers.org/combine.specifications/lems'
  },
  {
    id: 'RBA',
    name: 'RBA (Resource Balance Analysis)',
    description: 'Mathematical framework predicting resource allocation and metabolic fluxes in living cells.',
    extensions: ['.zip', '.xml'],
    accept: '.zip,.xml,application/zip',
    docsUrl: 'https://rba.inrae.fr/',
    sedUrn: 'urn:sedml:language:rba',
    omexManifestUri: 'http://identifiers.org/combine.specifications/rba'
  },
  {
    id: 'XPP',
    name: 'XPPAUT (ODE)',
    description: 'Phase plane and bifurcation tool for systems of differential and difference equations.',
    extensions: ['.ode'],
    accept: '.ode,text/plain',
    docsUrl: 'https://www.math.pitt.edu/~bard/xpp/xpp.html',
    sedUrn: 'urn:sedml:language:xpp',
    omexManifestUri: 'http://identifiers.org/combine.specifications/xpp'
  }
];

export interface FormatOption {
  name: string;
  description: string;
  extensions: string[];
  accept: string;
  docsUrl?: string;
}

export const SEDML_FORMAT_OPTION: FormatOption = {
  name: 'SED-ML (Simulation Experiment Description Markup Language)',
  description: 'Simulation Experiment Description Markup Language (SED-ML) defines models, simulation algorithms, tasks, outputs, and data generators.',
  extensions: ['.sedml', '.xml'],
  accept: '.sedml,.xml,text/xml,application/xml',
  docsUrl: 'https://sed-ml.org/'
};

export type OmexMetadataInputFormat = 'rdfxml' | 'turtle' | 'ntriples' | 'nquads' | 'rdfa';

export type OmexMetadataSchema = 'BioSimulations' | 'rdf_triples';

export interface OmexMetadataFormatOption {
  id: OmexMetadataInputFormat;
  name: string;
  description: string;
  extensions: string[];
  accept: string;
  docsUrl?: string;
}

export const OMEX_METADATA_FORMAT_OPTIONS: OmexMetadataFormatOption[] = [
  {
    id: 'rdfxml',
    name: 'RDF/XML',
    description: 'XML serialization of RDF graphs. Standard format required for publishing project metadata to BioSimulations.',
    extensions: ['.xml', '.rdf', '.rdfxml'],
    accept: '.xml,.rdf,.rdfxml,text/xml,application/rdf+xml,application/xml',
    docsUrl: 'https://www.w3.org/TR/rdf-syntax-grammar/'
  },
  {
    id: 'turtle',
    name: 'Turtle (Terse RDF Triple Language)',
    description: 'Compact, human-readable textual syntax for RDF graphs commonly used in semantic web tools.',
    extensions: ['.ttl'],
    accept: '.ttl,text/turtle',
    docsUrl: 'https://www.w3.org/TR/turtle/'
  },
  {
    id: 'ntriples',
    name: 'N-Triples',
    description: 'Line-based, plain text serialization format for RDF triples.',
    extensions: ['.nt'],
    accept: '.nt,application/n-triples,text/plain',
    docsUrl: 'https://www.w3.org/TR/n-triples/'
  },
  {
    id: 'nquads',
    name: 'N-Quads',
    description: 'Line-based format extending N-Triples to support RDF datasets with named graphs.',
    extensions: ['.nq'],
    accept: '.nq,application/n-quads,text/plain',
    docsUrl: 'https://www.w3.org/TR/n-quads/'
  },
  {
    id: 'rdfa',
    name: 'RDFa (RDF in Attributes)',
    description: 'W3C Recommendation for embedding structured metadata attributes within HTML/XML documents.',
    extensions: ['.html', '.xhtml', '.xml'],
    accept: '.html,.xhtml,.xml,text/html,application/xhtml+xml',
    docsUrl: 'https://www.w3.org/TR/rdfa-core/'
  }
];

export interface OmexMetadataSchemaOption {
  id: OmexMetadataSchema;
  name: string;
  description: string;
  docsUrl?: string;
}

export const OMEX_METADATA_SCHEMA_OPTIONS: OmexMetadataSchemaOption[] = [
  {
    id: 'BioSimulations',
    name: 'BioSimulations Schema (Recommended)',
    description: 'Enforces BioSimulations metadata conventions and minimal required properties (title, creators, description, license) required for publishing projects.',
    docsUrl: 'https://docs.biosimulations.org/concepts/conventions/simulation-project-metadata/'
  },
  {
    id: 'rdf_triples',
    name: 'RDF Triples (General Semantic)',
    description: 'Validates general RDF syntax and graph structure, allowing arbitrary semantic triples without domain-specific schema restrictions.',
    docsUrl: 'https://www.w3.org/RDF/'
  }
];

export const COMBINE_PROJECT_FORMAT_OPTION: FormatOption = {
  name: 'COMBINE / OMEX Archive',
  description: 'Open Modeling EXchange format (OMEX) archive bundling computational models, SED-ML simulation experiments, metadata annotations, and digital assets into a standard container.',
  extensions: ['.omex', '.zip'],
  accept: '.omex,.zip,application/zip,application/x-zip-compressed',
  docsUrl: 'https://combinearchive.org/'
};

export interface ValidateProjectOptions {
  omexMetadataFormat?: OmexMetadataInputFormat;
  omexMetadataSchema?: OmexMetadataSchema;
  validateOmexManifest?: boolean;
  validateSedml?: boolean;
  validateSedmlModels?: boolean;
  validateOmexMetadata?: boolean;
  validateImages?: boolean;
}

// =============================================================================
// KiSAO Algorithm Substitution & Simulator Suggestion
// =============================================================================

export enum AlgorithmSubstitutionPolicyLevels {
  NONE = 0,
  SAME_METHOD = 1,
  SAME_MATH = 2,
  SIMILAR_APPROXIMATIONS = 3,
  DISTINCT_APPROXIMATIONS = 4,
  DISTINCT_SCALES = 5,
  SAME_VARIABLES = 6,
  SIMILAR_VARIABLES = 7,
  SAME_FRAMEWORK = 8,
  ANY = 9,
}

export type AlgorithmSubstitutionPolicyId = 'NONE'
  | 'SAME_METHOD'
  | 'SAME_MATH'
  | 'SIMILAR_APPROXIMATIONS'
  | 'DISTINCT_APPROXIMATIONS'
  | 'DISTINCT_SCALES'
  | 'SAME_VARIABLES'
  | 'SIMILAR_VARIABLES'
  | 'SAME_FRAMEWORK'
  | 'ANY';

export interface AlgorithmSubstitutionPolicy {
  _type?: 'KisaoAlgorithmSubstitutionPolicy';
  level: number;
  id: AlgorithmSubstitutionPolicyId | string;
  name: string;
}

export interface AlgorithmSummary {
  _type?: 'KisaoTerm' | 'Algorithm';
  id: string;
  name: string;
}

export interface AlgorithmSubstitution {
  _type?: 'KisaoAlgorithmSubstitution';
  algorithms: AlgorithmSummary[];
  minPolicy: AlgorithmSubstitutionPolicy;
}

export interface AlgorithmItem {
  id: string;
  name: string;
  url: string;
}

export interface AlgorithmPolicyGroup {
  minPolicy: AlgorithmSubstitutionPolicy;
  algorithms: AlgorithmItem[];
}

export interface SimulatorMatchItem {
  id: string;
  name: string;
  url: string;
  algorithms: AlgorithmItem[];
}

export interface SimulatorPolicyGroup {
  minPolicy: AlgorithmSubstitutionPolicy;
  simulators: SimulatorMatchItem[];
}

export interface AlgorithmSuggestionData {
  algorithm: AlgorithmItem;
  altAlgorithms: AlgorithmPolicyGroup[];
  simulators: SimulatorPolicyGroup[];
}

export const ALGORITHM_SUBSTITUTION_POLICIES: AlgorithmSubstitutionPolicy[] = [
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 0,
    id: 'NONE',
    name: 'None',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 1,
    id: 'SAME_METHOD',
    name: 'Same method',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 2,
    id: 'SAME_MATH',
    name: 'Same math',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 3,
    id: 'SIMILAR_APPROXIMATIONS',
    name: 'Similar approximations',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 4,
    id: 'DISTINCT_APPROXIMATIONS',
    name: 'Distinct approximations',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 5,
    id: 'DISTINCT_SCALES',
    name: 'Distinct scales',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 6,
    id: 'SAME_VARIABLES',
    name: 'Same variables',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 7,
    id: 'SIMILAR_VARIABLES',
    name: 'Similar variables',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 8,
    id: 'SAME_FRAMEWORK',
    name: 'Same framework',
  },
  {
    _type: 'KisaoAlgorithmSubstitutionPolicy',
    level: 9,
    id: 'ANY',
    name: 'Any',
  },
];

// =============================================================================
// SED-ML & COMBINE Archive Data Models
// =============================================================================

export interface Namespace {
  _type?: 'Namespace';
  prefix?: string;
  uri: string;
}

export interface SedTarget {
  _type?: 'SedTarget';
  value: string;
  namespaces?: Namespace[];
}

export interface SedModelChange {
  _type?: 'SedModelAttributeChange' | string;
  id: string;
  name?: string;
  newValue?: string;
  target?: SedTarget;
  default?: string;
}

export interface SedModel {
  _type?: 'SedModel';
  id: string;
  language: string;
  source: string;
  changes?: SedModelChange[];
}

export interface SedAlgorithmParameterChange {
  _type?: 'SedAlgorithmParameterChange';
  kisaoId: string;
  newValue: string;
}

export interface SedAlgorithm {
  _type?: 'SedAlgorithm';
  kisaoId: string;
  changes?: SedAlgorithmParameterChange[];
}

export interface SedUniformTimeCourseSimulation {
  _type?: 'SedUniformTimeCourseSimulation';
  id: string;
  name?: string;
  initialTime: number;
  outputStartTime: number;
  outputEndTime: number;
  numberOfSteps: number;
  algorithm: SedAlgorithm;
}

export interface SedSteadyStateSimulation {
  _type?: 'SedSteadyStateSimulation';
  id: string;
  name?: string;
  algorithm: SedAlgorithm;
}

export interface SedOneStepSimulation {
  _type?: 'SedOneStepSimulation';
  id: string;
  name?: string;
  step: number;
  algorithm: SedAlgorithm;
}

export type SedSimulation
  = | SedUniformTimeCourseSimulation
    | SedSteadyStateSimulation
    | SedOneStepSimulation;

export interface SedTask {
  _type?: 'SedTask';
  id: string;
  name?: string;
  model: string;
  simulation: string;
}

export interface SedVariable {
  _type?: 'SedVariable';
  id: string;
  name?: string;
  symbol?: string;
  target?: SedTarget;
  model?: string;
  task?: string;
}

export interface SedDataGenerator {
  _type?: 'SedDataGenerator';
  id: string;
  name?: string;
  math: string;
  parameters?: any[];
  variables: SedVariable[];
}

export interface SedDataSet {
  _type?: 'SedDataSet';
  id: string;
  label: string;
  name?: string;
  dataGenerator: string;
}

export interface SedCurve {
  _type?: 'SedCurve';
  id: string;
  name?: string;
  xDataGenerator: string;
  yDataGenerator: string;
  style?: string;
}

export interface SedReport {
  _type?: 'SedReport';
  id: string;
  name?: string;
  dataSets: SedDataSet[];
}

export interface SedPlot2D {
  _type?: 'SedPlot2D';
  id: string;
  name?: string;
  curves: SedCurve[];
  xScale?: string;
  yScale?: string;
}

export type SedOutput = SedReport | SedPlot2D;

export interface SedDocument {
  _type?: 'SedDocument';
  level: number;
  version: number;
  styles?: any[];
  models: SedModel[];
  simulations: SedSimulation[];
  tasks: SedTask[];
  dataGenerators: SedDataGenerator[];
  outputs: SedOutput[];
}

export interface CombineArchiveContentLocationValueFile {
  _type: 'CombineArchiveContentFile';
  filename: string;
}

export interface CombineArchiveContentLocationValueUrl {
  _type: 'CombineArchiveContentUrl';
  url: string;
}

export type CombineArchiveContentLocationValue
  = | CombineArchiveContentLocationValueFile
    | CombineArchiveContentLocationValueUrl
    | SedDocument;

export interface CombineArchiveLocation {
  _type?: 'CombineArchiveLocation';
  path: string;
  value: CombineArchiveContentLocationValue;
}

export interface CombineArchiveContent {
  _type?: 'CombineArchiveContent';
  format: string;
  master: boolean;
  location: CombineArchiveLocation;
}

export interface CombineArchive {
  _type?: 'CombineArchive';
  contents: CombineArchiveContent[];
}

// =============================================================================
// Simulation Frameworks, Types & Algorithm Presets
// =============================================================================

export interface ModelingFrameworkOption {
  id: string;
  name: string;
  description: string;
}

export const MODELING_FRAMEWORKS: ModelingFrameworkOption[] = [
  {
    id: 'SBO_0000293',
    name: 'Non-spatial deterministic kinetics',
    description: 'Continuous deterministic simulation using ordinary differential equations (ODEs).'
  },
  {
    id: 'SBO_0000294',
    name: 'Non-spatial discrete stochastic kinetics',
    description: 'Stochastic chemical kinetics using exact algorithms (e.g. Gillespie direct method).'
  },
  {
    id: 'SBO_0000295',
    name: 'Flux balance analysis (FBA)',
    description: 'Constraint-based stoichiometric analysis of genome-scale metabolic networks.'
  },
  {
    id: 'SBO_0000292',
    name: 'Continuous spatial kinetics',
    description: 'Spatial deterministic reaction-diffusion dynamics modeled with partial differential equations.'
  },
  {
    id: 'SBO_0000297',
    name: 'Discrete spatial kinetics',
    description: 'Spatial stochastic particle simulation (e.g. Smoldyn).'
  },
  {
    id: 'SBO_0000547',
    name: 'Logical / Boolean modeling',
    description: 'Qualitative discrete dynamical modeling of gene regulatory networks.'
  }
];

export type SimulationTypeOption
  = | 'SedUniformTimeCourseSimulation'
    | 'SedSteadyStateSimulation'
    | 'SedOneStepSimulation';

export interface SimulationTypeDescriptor {
  id: SimulationTypeOption;
  name: string;
  description: string;
  icon: string;
}

export const SIMULATION_TYPES: SimulationTypeDescriptor[] = [
  {
    id: 'SedUniformTimeCourseSimulation',
    name: 'Uniform Time Course',
    description: 'Simulate system state dynamics over a specified time duration divided into equidistant output intervals.',
    icon: 'i-lucide-timer'
  },
  {
    id: 'SedSteadyStateSimulation',
    name: 'Steady State',
    description: 'Determine the time-invariant equilibrium state or flux distribution of the system.',
    icon: 'i-lucide-activity'
  },
  {
    id: 'SedOneStepSimulation',
    name: 'One Step',
    description: 'Execute a single advance step in the simulation trajectory.',
    icon: 'i-lucide-step-forward'
  }
];

export interface SimulationAlgorithmPreset {
  id: string;
  name: string;
  frameworkId: string;
  description: string;
}

export const SIMULATION_ALGORITHMS: SimulationAlgorithmPreset[] = [
  {
    id: 'KISAO_0000019',
    name: 'CVODE (Variable-step ODE solver)',
    frameworkId: 'SBO_0000293',
    description: 'Adaptive step-size ordinary differential equation solver from SUNDIALS.'
  },
  {
    id: 'KISAO_0000029',
    name: 'Gillespie direct method (Stochastic SSA)',
    frameworkId: 'SBO_0000294',
    description: 'Exact stochastic simulation algorithm tracking individual discrete reaction events.'
  },
  {
    id: 'KISAO_0000560',
    name: 'Flux balance analysis (GLPK/CPLEX)',
    frameworkId: 'SBO_0000295',
    description: 'Linear programming optimization for steady-state metabolic flux distribution.'
  },
  {
    id: 'KISAO_0000032',
    name: 'Explicit Runge-Kutta 4th order',
    frameworkId: 'SBO_0000293',
    description: 'Classical fixed step-size 4-stage numerical integration algorithm.'
  },
  {
    id: 'KISAO_0000030',
    name: 'Gibson-Bruck next reaction method',
    frameworkId: 'SBO_0000294',
    description: 'Efficient exact stochastic formulation using a dependency graph and indexed priority queue.'
  },
  {
    id: 'KISAO_0000088',
    name: 'Tau-leaping approximate stochastic method',
    frameworkId: 'SBO_0000294',
    description: 'Accelerated approximate simulation grouping multiple sub-steps within leap intervals.'
  },
  {
    id: 'KISAO_0000450',
    name: 'Asynchronous logical simulation',
    frameworkId: 'SBO_0000547',
    description: 'Discrete qualitative simulation updating single state variables nondeterministically.'
  }
];



