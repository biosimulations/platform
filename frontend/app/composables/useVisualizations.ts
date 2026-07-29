import type { ProjectFile, SimulationRunSedDocument } from '~/models/simulation';
import type { VisualizationList, VegaVisualization, SedPlot2DVisualization, Visualization } from '~/models/visualizations';
import {getPlotlyDataLayout} from '~/functions/sed-plot-2d-visualization';
import { reactive } from 'vue';

export async function useVisualizations(
  runId: string,
  files: ProjectFile[],
  sedDocs: SimulationRunSedDocument | SimulationRunSedDocument[] | undefined
): Promise<VisualizationList[]> {

  const config = useRuntimeConfig();
  const api_url = config.public.legacy_api_url;

  // Normalize sedDocs to array
  const sedmlArchiveContents: SimulationRunSedDocument[] = [];
  if (Array.isArray(sedDocs)) {
    sedmlArchiveContents.push(...sedDocs);
  } else if (sedDocs) {
    sedmlArchiveContents.push(sedDocs);
  }

  // 1. Process Vega Visualizations
  const vegaUris = [
    'http://purl.org/NET/mediatypes/application/vnd.vega.v5+json',
    'http://purl.org/NET/mediatypes/application/vnd.vega.v4+json',
    'http://purl.org/NET/mediatypes/application/vnd.vega.v3+json',
    'http://purl.org/NET/mediatypes/application/vnd.vega.v2+json',
    'http://purl.org/NET/mediatypes/application/vnd.vega.v1+json',
  ];

  const vegaFiles = files.filter(f =>
    vegaUris.includes(f.format) || f.format.toLowerCase().includes('vega')
  );

  const vegaVisualizations: VegaVisualization[] = vegaFiles.map((file): VegaVisualization => {
      let fileLocation = file.location;
      if (fileLocation.startsWith('./')) {
        fileLocation = fileLocation.substring(2);
      }

      const vis = reactive<VegaVisualization>({
        _type: 'VegaVisualization',
        id: fileLocation,
        name: fileLocation,
        renderer: 'Vega',
        vegaSpec: null,
        _isLoading: true
      });

      // Fetch the actual Vega Spec asynchronously
      $fetch(file.url).then((spec) => {
        vis.vegaSpec = spec;
        vis._isLoading = false;
      }).catch((err) => {
        console.error('Failed to fetch vega spec for ' + file.location, err);
        vis._isLoading = false;
        vis._error = 'Failed to load';
      });

      return vis;
    }
  );

  const vegaVisualizationsList: VisualizationList[] = vegaVisualizations.length
? [{
    title: 'Vega charts',
    visualizations: vegaVisualizations
  }]
: [];

  // 2. Process SED-ML Visualizations
  const sedmlVisualizationsList: VisualizationList[] = [];

  for (const sedDoc of sedmlArchiveContents) {
    let location = sedDoc.id;
    if (location.startsWith('./')) {
      location = location.substring(2);
    }

    const visualizations: Visualization[] = [];

    for (const output of sedDoc.outputs) {
      if (output._type === 'SedPlot2D') {
        const vis = reactive<SedPlot2DVisualization>({
          _type: 'SedPlot2DVisualization',
          id: `${location}/${output.id}`,
          name: `${output.name || output.id}`,
          renderer: 'Plotly',
          plotlyDataLayout: { dataErrors: ['Loading...'] } as any,
          _isLoading: true
        });

        visualizations.push(vis);

        // Fetch asynchronously
        const outputIdParam = encodeURIComponent(location + '/' + output.id);
        const resultUrl = `${api_url}/results/${runId}/${outputIdParam}?includeData=true`;

        $fetch(resultUrl).then((results: any) => {
          vis.plotlyDataLayout = getPlotlyDataLayout(runId, location, output as any, results);
          vis._isLoading = false;
        }).catch((err) => {
          console.error(`Failed to generate Plotly data for ${output.id}`, err);
          vis.plotlyDataLayout = { dataErrors: ['The data for the plot could not be loaded.'] } as any;
          vis._isLoading = false;
          vis._error = 'Failed to load';
        });
      }
    }

    if (visualizations.length > 0) {
      sedmlVisualizationsList.push({
        title: 'SED-ML charts for ' + location,
        visualizations: visualizations
      });
    }
  }

  // 3. Process Design a chart
  const uriSedDataSetMap: any = {};
  let hasData2D = false;

  sedmlArchiveContents.forEach((sedDoc) => {
    sedDoc.outputs.forEach((output: any) => {
      if (output._type === 'SedReport') {
        hasData2D = true;
        output.dataSets.forEach((dataSet: any) => {
          let location = sedDoc.id;
          if (location.startsWith('./')) {
            location = location.substring(2);
          }
          const uri = location + '/' + output.id + '/' + dataSet.id;
          uriSedDataSetMap[uri] = {
            id: dataSet.id,
            name: dataSet.name || dataSet.id,
            uri: uri,
            label: dataSet.label || dataSet.name || dataSet.id
          };
        });
      }
    });
  });

  const designVisualizationsList: VisualizationList[] = [];
  if (hasData2D) {
    const designVisualizations: any[] = [
      {
        _type: 'Line2DVisualization',
        id: 'Line2DVisualization',
        name: '2D line plot',
        renderer: 'Plotly',
        userDesigned: true,
        simulationRunId: runId,
        sedDocs: sedmlArchiveContents,
        uriSedDataSetMap: uriSedDataSetMap
      },
      {
        _type: 'Heatmap2DVisualization',
        id: 'Heatmap2DVisualization',
        name: '2D heatmap',
        renderer: 'Plotly',
        userDesigned: true,
        simulationRunId: runId,
        sedDocs: sedmlArchiveContents,
        uriSedDataSetMap: uriSedDataSetMap
      },
      {
        _type: 'Histogram1DVisualization',
        id: 'Histogram1DVisualization',
        name: '1D histogram',
        renderer: 'Plotly',
        userDesigned: true,
        simulationRunId: runId,
        sedDocs: sedmlArchiveContents,
        uriSedDataSetMap: uriSedDataSetMap
      }
    ];

    designVisualizationsList.push({
      title: 'Design a chart',
      visualizations: designVisualizations
    });
  }

  // 4. Return combined list
  return [...vegaVisualizationsList, ...sedmlVisualizationsList, ...designVisualizationsList];
}
