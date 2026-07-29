import type {PlotlyDataLayout} from "~/models/plotly";
import type {SimulationRunSedDocument} from "~/models/simulation";

export interface VegaVisualization {
  _type: 'VegaVisualization';
  id: string;
  name: string;
  renderer: 'Vega';
  vegaSpec: any;
  userDesigned?: false;
  _isLoading?: boolean;
  _error?: string;
}

export interface SedPlot2DVisualization {
  _type: 'SedPlot2DVisualization';
  id: string;
  name: string;
  renderer: 'Plotly';
  plotlyDataLayout: PlotlyDataLayout;
  userDesigned?: false;
  _isLoading?: boolean;
  _error?: string;
}

export interface UserDesignedVisualization {
  _type: 'Line2DVisualization' | 'Heatmap2DVisualization' | 'Histogram1DVisualization';
  id: string;
  name: string;
  renderer: 'Plotly';
  userDesigned: true;
  simulationRunId: string;
  sedDocs: SimulationRunSedDocument[];
  uriSedDataSetMap: any;
}

export type Visualization = VegaVisualization | SedPlot2DVisualization | UserDesignedVisualization;

export interface VisualizationList {
  title: string;
  visualizations: Visualization[];
}
