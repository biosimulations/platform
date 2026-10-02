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
