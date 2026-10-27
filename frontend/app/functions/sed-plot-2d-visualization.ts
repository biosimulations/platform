import type { PageSedOutput, PageSedCurveStyle } from '~/models/page';
import { flattenTaskResults, getRepeatedTaskTraceLabel } from './utils';
import hexToRgba from 'hex-to-rgba';
import {type PlotlyDataLayout, type PlotlyTrace, type PlotlyTraceLineDash, type PlotlyTraceMarkerSymbol, PlotlyTraceMode, PlotlyTraceType} from "~/models/plotly";

// Basic typing for SimulationRunOutput since it might not be fully mapped in Vue yet
export interface SimulationRunOutputDatumElement {
  [key: string]: any;
}

export interface SimulationRunOutputDatum {
  id: string;
  label: string;
  name?: string;
  shape: string;
  type: string;
  values: any[];
}

export interface SimulationRunOutput {
  simId: string;
  outputId: string;
  name?: string;
  type: string;
  data: SimulationRunOutputDatum[];
}

interface SedDatasetResults {
  uri: string;
  id: string;
  location: string;
  outputId: string;
  label: string;
  values: any[];
}

interface SedDatasetResultsMap {
  [uri: string]: SedDatasetResults;
}

const sedLineStyleTypePlotlyMap: {
  [sedType: string]: PlotlyTraceLineDash | undefined;
} = {
  none: undefined,
  solid: 'solid',
  dash: 'dash',
  dot: 'dot',
  dashDot: 'dashdot',
  dashDotDot: 'longdashdot',
};

const sedMarkerStyleTypePlotlyMap: {
  [sedType: string]: PlotlyTraceMarkerSymbol | undefined;
} = {
  none: undefined,
  square: 'square',
  circle: 'circle',
  diamond: 'diamond',
  xCross: 'x',
  plus: 'cross',
  star: 'star',
  triangleUp: 'triangle-up',
  triangleDown: 'triangle-down',
  triangleLeft: 'triangle-left',
  triangleRight: 'triangle-right',
  hDash: 'line-ew',
  vDash: 'line-ns',
};

export function getPlotlyDataLayout(
  simulationRunId: string,
  sedDocLocation: string,
  plot: PageSedOutput,
  results: SimulationRunOutput,
): PlotlyDataLayout {
  if (sedDocLocation.startsWith('./')) {
    sedDocLocation = sedDocLocation.substring(2);
  }

  const resultsMap: SedDatasetResultsMap = getSimulationRunResults(results);

  const traces: PlotlyTrace[] = [];
  const xAxisTitlesSet = new Set<string>();
  const yAxisTitlesSet = new Set<string>();
  const errors: string[] = [];
  for (const curve of plot.curves || []) {
    const xGenId = typeof curve.xDataGenerator === 'string' ? curve.xDataGenerator : curve.xDataGenerator.id;
    const yGenId = typeof curve.yDataGenerator === 'string' ? curve.yDataGenerator : curve.yDataGenerator.id;

    const xId = sedDocLocation + '/' + plot.id + '/' + xGenId;
    const yId = sedDocLocation + '/' + plot.id + '/' + yGenId;

    const xData = resultsMap?.[xId]?.values;
    const yData = resultsMap?.[yId]?.values;

    if (xData && yData) {
      const xGenName = typeof curve.xDataGenerator === 'string' ? curve.xDataGenerator : (curve.xDataGenerator.name || curve.xDataGenerator.id);
      const yGenName = typeof curve.yDataGenerator === 'string' ? curve.yDataGenerator : (curve.yDataGenerator.name || curve.yDataGenerator.id);

      xAxisTitlesSet.add(xGenName);
      yAxisTitlesSet.add(yGenName);

      const style = curve?.style ? resolveStyle(curve.style) : undefined;

      const flatData = flattenTaskResults([xData, yData]);

      for (let iTrace = 0; iTrace < (flatData.data[0]?.length || 0); iTrace++) {
        const name
          = (curve.name || curve.id)
            + (flatData.data[0]!.length > 1 ? ` (${getRepeatedTaskTraceLabel(iTrace, flatData.outerShape)})` : '');
        const trace: PlotlyTrace = {
          name: name,
          x: flatData.data[0]![iTrace],
          y: flatData.data[1]![iTrace],
          xaxis: 'x1',
          yaxis: 'y1',
          type: PlotlyTraceType.scatter,
        };

        const hasLine = !(style?.line && style.line?.type === 'none');
        const hasMarker = style?.marker && style.marker?.type && sedMarkerStyleTypePlotlyMap?.[style.marker.type];

        if (hasLine || hasMarker) {
          if (hasLine) {
            if (hasMarker) {
              trace.mode = PlotlyTraceMode.linesMarkers;
            } else {
              trace.mode = PlotlyTraceMode.lines;
            }
          } else {
            trace.mode = PlotlyTraceMode.markers;
          }
        } else {
          trace.mode = PlotlyTraceMode.none;
        }

        if (hasLine) {
          trace.line = {
            dash: style?.line?.type ? sedLineStyleTypePlotlyMap?.[style.line.type] : undefined,
            color: style?.line?.color ? hexToRgba(style.line.color) : undefined,
            width: style?.line?.thickness,
          };
        }

        if (hasMarker) {
          trace.marker = {
            symbol: style?.marker?.type ? sedMarkerStyleTypePlotlyMap[style.marker.type] : undefined,
            size: style?.marker?.size,
            color: style?.marker?.fillColor ? hexToRgba(style.marker.fillColor) : undefined,
          };

          if (style.marker?.lineColor || style.marker?.lineThickness) {
            trace.marker.line = {
              color: style.marker?.lineColor ? hexToRgba(style.marker?.lineColor) : undefined,
              width: style.marker?.lineThickness,
            };
          }
        }

        if (style?.fill) {
          console.warn(
            `Fill was ignored for curve '${curve.id}' of plot '${plot.id}'. SED-ML does not support fills for point curves.`,
          );
        }

        traces.push(trace);
      }
    } else {
      errors.push(`Curve '${curve.id}' of '${xId}' and '${yId}'.`);
    }
  }

  const xAxisTitlesArr = Array.from(xAxisTitlesSet);
  const yAxisTitlesArr = Array.from(yAxisTitlesSet);
  let xAxisTitle: string | undefined;
  let yAxisTitle: string | undefined;
  let showLegend = false;

  if (xAxisTitlesArr.length == 1) {
    xAxisTitle = xAxisTitlesArr[0];
  } else if (xAxisTitlesArr.length > 1) {
    xAxisTitle = 'Multiple';
    showLegend = true;
  }

  if (yAxisTitlesArr.length == 1) {
    yAxisTitle = yAxisTitlesArr[0];
  } else if (yAxisTitlesArr.length > 1) {
    yAxisTitle = 'Multiple';
    showLegend = true;
  }

  const dataLayout: PlotlyDataLayout = {
    data: traces.length ? traces : undefined,
    layout: {
      xaxis1: {
        anchor: 'x1',
        title: xAxisTitle,
        type: plot.xScale as any,
      },
      yaxis1: {
        anchor: 'y1',
        title: yAxisTitle,
        type: plot.yScale as any,
      },
      grid: {
        rows: 1,
        columns: 1,
        pattern: 'independent',
      },
      showlegend: showLegend,
      width: undefined,
      height: undefined,
    },
    dataErrors: errors.length > 0 ? errors : undefined,
  };

  return dataLayout;
}

function getSimulationRunResults(result: SimulationRunOutput): SedDatasetResultsMap {
  const datasetResultsMap: SedDatasetResultsMap = {};

  const sedmlLocationOutputId = result.outputId;
  const sedmlLocation = getLocationFromSedmLocationId(sedmlLocationOutputId);
  const outputId = getOutputIdFromSedmlLocationId(sedmlLocationOutputId);

  result.data.forEach((datum: SimulationRunOutputDatum): void => {
    const uri = sedmlLocation + '/' + outputId + '/' + datum.id;
    datasetResultsMap[uri] = {
      uri: uri,
      id: datum.id,
      location: sedmlLocation,
      outputId: outputId,
      label: datum.label,
      values: datum.values,
    };
  });

  return datasetResultsMap;
}

function getLocationFromSedmLocationId(outputLocationId: string): string {
  let docLocation = outputLocationId.split('/').reverse().slice(1).reverse().join('/');
  if (docLocation.startsWith('./')) {
    docLocation = docLocation.substring(2);
  }
  return docLocation;
}

function getOutputIdFromSedmlLocationId(location: string): string {
  return location.split('/').reverse()[0] || '';
}

function resolveStyle(style: PageSedCurveStyle | string): any {
  let resolvedStyle: any;

  if (typeof style === 'string') {
    return {
      _type: 'SedStyle',
      id: style,
    };
  }

  if (style?.base) {
    resolvedStyle = resolveStyle(style.base);
  } else {
    resolvedStyle = {
      _type: 'SedStyle',
    };
  }

  resolvedStyle.base = style?.base;

  if (style?.line !== undefined && style?.line !== null) {
    if (resolvedStyle?.line === undefined) {
      resolvedStyle.line = {
        _type: 'SedLineStyle',
      };
    }
    if (style.line.type !== undefined && style.line.type !== null) {
      resolvedStyle.line.type = style.line.type;
    }
    if (style.line.color !== undefined && style.line.color !== null) {
      resolvedStyle.line.color = style.line.color;
    }
    if (style.line.thickness !== undefined && style.line.thickness !== null) {
      resolvedStyle.line.thickness = style.line.thickness;
    }
  }

  if (style?.marker !== undefined && style?.marker !== null) {
    if (resolvedStyle?.marker === undefined) {
      resolvedStyle.marker = {
        _type: 'SedMarkerStyle',
      };
    }
    if (style.marker.type !== undefined && style.marker.type !== null) {
      resolvedStyle.marker.type = style.marker.type;
    }
    if (style.marker.size !== undefined && style.marker.size !== null) {
      resolvedStyle.marker.size = style.marker.size;
    }
    if (style.marker.lineColor !== undefined && style.marker.lineColor !== null) {
      resolvedStyle.marker.lineColor = style.marker.lineColor;
    }
    if (style.marker.lineThickness !== undefined && style.marker.lineThickness !== null) {
      resolvedStyle.marker.lineThickness = style.marker.lineThickness;
    }
    if (style.marker.fillColor !== undefined && style.marker.fillColor !== null) {
      resolvedStyle.marker.fillColor = style.marker.fillColor;
    }
  }

  return resolvedStyle;
}
