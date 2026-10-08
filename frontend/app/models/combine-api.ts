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
    docsUrl: 'https://sbml.org/'
  },
  {
    id: 'CellML',
    name: 'CellML',
    description: 'XML-based format for describing mathematical models of cellular and subcellular processes.',
    extensions: ['.cellml', '.xml'],
    accept: '.cellml,.xml,text/xml,application/xml',
    docsUrl: 'https://www.cellml.org/'
  },
  {
    id: 'BNGL',
    name: 'BNGL (BioNetGen Language)',
    description: 'Rule-based modeling language for specifying biochemical systems with combinatorial complexity.',
    extensions: ['.bngl'],
    accept: '.bngl,text/plain',
    docsUrl: 'https://bionetgen.org/'
  },
  {
    id: 'Smoldyn',
    name: 'Smoldyn',
    description: 'Particle-based spatial stochastic simulator for biochemical and biophysical systems.',
    extensions: ['.smoldyn', '.txt'],
    accept: '.smoldyn,.txt,text/plain',
    docsUrl: 'https://www.smoldyn.org/'
  },
  {
    id: 'GINsim',
    name: 'GINsim (GINML)',
    description: 'Qualitative simulation software for genetic and regulatory logical networks.',
    extensions: ['.ginml', '.zginml', '.xml'],
    accept: '.ginml,.zginml,.xml,text/xml,application/xml',
    docsUrl: 'http://ginsim.org/'
  },
  {
    id: 'NeuroML',
    name: 'NeuroML',
    description: 'XML-based description language for computational neuroscience models.',
    extensions: ['.nml', '.xml'],
    accept: '.nml,.xml,text/xml,application/xml',
    docsUrl: 'https://docs.neuroml.org/'
  },
  {
    id: 'LEMS',
    name: 'LEMS (Low Entropy Model Specification)',
    description: 'Language for expressing mathematical definitions and simulations of dynamic models.',
    extensions: ['.lems', '.xml'],
    accept: '.lems,.xml,text/xml,application/xml',
    docsUrl: 'https://lems.github.io/LEMS/'
  },
  {
    id: 'RBA',
    name: 'RBA (Resource Balance Analysis)',
    description: 'Mathematical framework predicting resource allocation and metabolic fluxes in living cells.',
    extensions: ['.zip', '.xml'],
    accept: '.zip,.xml,application/zip',
    docsUrl: 'https://rba.inrae.fr/'
  },
  {
    id: 'XPP',
    name: 'XPPAUT (ODE)',
    description: 'Phase plane and bifurcation tool for systems of differential and difference equations.',
    extensions: ['.ode'],
    accept: '.ode,text/plain',
    docsUrl: 'https://www.math.pitt.edu/~bard/xpp/xpp.html'
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


