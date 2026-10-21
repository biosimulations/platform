<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';
import type { StepperItem } from '#ui/components/Stepper.vue';
import type { TabsItem } from '#ui/components/Tabs.vue';
import type { TableColumn } from '@nuxt/ui';
import type { TableMeta, Row } from '@tanstack/vue-table';
import {
  MODEL_LANGUAGE_OPTIONS,
  MODELING_FRAMEWORKS,
  SIMULATION_TYPES,
  SIMULATION_ALGORITHMS,
  type ModelLanguageOption,
  type SimulationTypeOption,
  type SedDocument,
  type SedModel,
  type SedModelChange,
  type SedVariable,
  type SedUniformTimeCourseSimulation,
  type SedSimulation,
  type SedTask,
  type SedDataGenerator,
  type SedDataSet,
  type SedCurve,
  type CombineArchive,
  type Namespace
} from '~/models/combine-api';
import { useCombineApi } from '~/composables/useCombineApi';

useSeoMeta({
  title: 'New Simulation Experiment - BioSimulations',
  description: 'Design and configure SED-ML simulation experiments, introspect model parameters and observable variables, and export a ready-to-run COMBINE archive (.omex).'
});

const route = useRoute();
const toast = useToast();
const { introspectModel, createCombineArchive } = useCombineApi();

const breadcrumbs: BreadcrumbItem[] = [
  {
    label: 'Home',
    icon: 'i-lucide-home',
    to: '/'
  },
  {
    label: 'Utilities',
    to: '/utilities'
  },
  {
    label: 'New Simulation Experiment'
  }
];

// Stepper setup
const activeStep = ref(0);
const stepper = useTemplateRef('stepper');

const steps: StepperItem[] = [
  {
    slot: 'model',
    title: 'Model',
    description: 'Select model source',
    icon: 'i-lucide-file-code'
  },
  {
    slot: 'method',
    title: 'Method',
    description: 'Framework & algorithm',
    icon: 'i-lucide-cpu'
  },
  {
    slot: 'experiment',
    title: 'Experiment',
    description: 'Time course conditions',
    icon: 'i-lucide-sliders'
  },
  {
    slot: 'customization',
    title: 'Customization',
    description: 'Parameters & variables',
    icon: 'i-lucide-list-checks'
  },
  {
    slot: 'download',
    title: 'Export',
    description: 'Build & download',
    icon: 'i-lucide-download'
  }
];

// =============================================================================
// Step 1: Model Source & Specification
// =============================================================================

const inputMode = ref<'file' | 'url'>('file');
const inputModes: TabsItem[] = [
  { label: 'Upload File', value: 'file', icon: 'i-lucide-upload-cloud' },
  { label: 'Public URL', value: 'url', icon: 'i-lucide-link' }
];

const selectedFile = ref<File | null>(null);
const modelUrl = ref('');
const urlError = ref<string | null>(null);
const archiveName = ref('simulation-experiment');
const selectedLanguage = ref<ModelLanguageOption>(MODEL_LANGUAGE_OPTIONS[0]!);

function sanitizeArchiveName(val: string): string {
  return val
    .trim()
    .replace(/\s+/g, '-')
    .replace(/[^a-zA-Z0-9_.-]/g, '');
}

function detectFormatFromFilename(name: string) {
  const lower = name.toLowerCase();
  for (const lang of MODEL_LANGUAGE_OPTIONS) {
    if (lang.extensions.some(ext => lower.endsWith(ext))) {
      selectedLanguage.value = lang;
      return;
    }
  }
}

watch(selectedFile, (file) => {
  if (file) {
    detectFormatFromFilename(file.name);
    const baseName = file.name.substring(0, file.name.lastIndexOf('.')) || file.name;
    if (archiveName.value === 'simulation-experiment' || !archiveName.value) {
      archiveName.value = sanitizeArchiveName(baseName);
    }
  }
});

watch(modelUrl, (val) => {
  if (val.trim()) {
    try {
      const parsed = new URL(val.trim());
      const segments = parsed.pathname.split('/').filter(Boolean);
      const filename = segments[segments.length - 1];
      if (filename) {
        detectFormatFromFilename(filename);
        const baseName = filename.substring(0, filename.lastIndexOf('.')) || filename;
        if (archiveName.value === 'simulation-experiment' || !archiveName.value) {
          archiveName.value = sanitizeArchiveName(baseName);
        }
      }
      urlError.value = null;
    } catch {
      urlError.value = 'Please enter a valid URL (e.g. https://example.org/model.xml)';
    }
  } else {
    urlError.value = null;
  }
});

const canAdvanceStep1 = computed(() => {
  if (inputMode.value === 'file') {
    if (!selectedFile.value) return false;
  } else {
    if (!modelUrl.value.trim() || Boolean(urlError.value)) return false;
  }
  return Boolean(archiveName.value.trim()) && Boolean(selectedLanguage.value);
});

// =============================================================================
// Step 2: Simulation Method (Framework, Type, Algorithm)
// =============================================================================

const selectedFrameworkId = ref<string>('SBO_0000293');
const selectedSimType = ref<SimulationTypeOption>('SedUniformTimeCourseSimulation');
const selectedAlgorithmId = ref<string>('KISAO_0000019');
const customAlgorithmInput = ref<string>('');

const currentFramework = computed(() =>
  MODELING_FRAMEWORKS.find(f => f.id === selectedFrameworkId.value) || MODELING_FRAMEWORKS[0]!
);

const currentSimType = computed(() =>
  SIMULATION_TYPES.find(t => t.id === selectedSimType.value) || SIMULATION_TYPES[0]!
);

const relevantAlgorithms = computed(() => {
  return SIMULATION_ALGORITHMS.filter(alg => alg.frameworkId === selectedFrameworkId.value);
});

watch(selectedFrameworkId, (newFw) => {
  const match = SIMULATION_ALGORITHMS.find(alg => alg.frameworkId === newFw);
  if (match) {
    selectedAlgorithmId.value = match.id;
  }
});

const canAdvanceStep2 = computed(() => {
  return (
    Boolean(selectedFrameworkId.value)
    && Boolean(selectedSimType.value)
    && Boolean(selectedAlgorithmId.value.trim())
  );
});

// =============================================================================
// Step 3: Experiment Setup & Introspection
// =============================================================================

const isIntrospecting = ref(false);
const introspectionDone = ref(false);
const introspectionError = ref<string | null>(null);

const timeCourse = reactive({
  initialTime: 0,
  outputStartTime: 0,
  outputEndTime: 10,
  numberOfSteps: 100
});

const stepSize = computed(() => {
  const span = timeCourse.outputEndTime - timeCourse.outputStartTime;
  if (timeCourse.numberOfSteps <= 0) return 0;
  return Number((span / timeCourse.numberOfSteps).toFixed(4));
});

const canAdvanceStep3 = computed(() => {
  if (selectedSimType.value === 'SedUniformTimeCourseSimulation') {
    return (
      timeCourse.outputEndTime > timeCourse.outputStartTime
      && timeCourse.numberOfSteps >= 1
      && timeCourse.initialTime >= 0
    );
  }
  return true;
});

// =============================================================================
// Step 4: Customization (Parameters & Observables)
// =============================================================================

interface EditableParameter {
  id: string;
  name: string;
  target: string;
  defaultValue: string;
  newValue: string;
  isCustom?: boolean;
}

interface SelectableVariable {
  id: string;
  name: string;
  symbol?: string;
  target?: string;
  selected: boolean;
}

const introspectedParameters = ref<EditableParameter[]>([]);
const introspectedNamespaces = ref<Namespace[]>([]);
const observableVariables = ref<SelectableVariable[]>([]);

const parameterSearch = ref('');
const variableSearch = ref('');

const filteredParameters = computed(() => {
  const q = parameterSearch.value.trim().toLowerCase();
  if (!q) return introspectedParameters.value;
  return introspectedParameters.value.filter(
    p => p.id.toLowerCase().includes(q) || p.name.toLowerCase().includes(q) || p.target.toLowerCase().includes(q)
  );
});

const filteredVariables = computed(() => {
  const q = variableSearch.value.trim().toLowerCase();
  if (!q) return observableVariables.value;
  return observableVariables.value.filter(
    v => v.id.toLowerCase().includes(q) || v.name.toLowerCase().includes(q) || (v.target && v.target.toLowerCase().includes(q))
  );
});

const activeParameterChanges = computed(() => {
  return introspectedParameters.value.filter(p => p.newValue.trim().length > 0 && (p.isCustom || p.newValue.trim() !== p.defaultValue));
});

const effectiveNamespaces = computed(() => {
  if (introspectedNamespaces.value.length > 0) return introspectedNamespaces.value;
  if (selectedLanguage.value.id === 'SBML') {
    return [{ prefix: 'sbml', uri: 'http://www.sbml.org/sbml/level3/version1/core' }];
  }
  return [];
});

const selectedVariablesCount = computed(() => {
  return observableVariables.value.filter(v => v.selected).length;
});

function selectAllVariables() {
  observableVariables.value.forEach((v) => {
    v.selected = true;
  });
}

function deselectAllVariables() {
  observableVariables.value.forEach((v) => {
    v.selected = false;
  });
}

const isCustomParamModalOpen = ref(false);
const editingCustomParamIndex = ref<number | null>(null);
const isIdTouched = ref(false);
const customParamForm = reactive({
  id: '',
  name: '',
  target: '',
  newValue: ''
});
const customParamError = ref<string | null>(null);

function openAddCustomParamModal() {
  editingCustomParamIndex.value = null;
  isIdTouched.value = false;
  const count = introspectedParameters.value.filter(p => p.isCustom).length + 1;
  customParamForm.id = `custom_param_${count}`;
  customParamForm.name = `Custom Parameter ${count}`;

  if (selectedLanguage.value.id === 'SBML') {
    customParamForm.target = `/sbml:sbml/sbml:model/sbml:listOfParameters/sbml:parameter[@id='param_${count}']/@value`;
  } else {
    customParamForm.target = `/model/parameters/param_${count}/@value`;
  }

  customParamForm.newValue = '1.0';
  customParamError.value = null;
  isCustomParamModalOpen.value = true;
}

function openEditCustomParamModal(param: EditableParameter) {
  const index = introspectedParameters.value.indexOf(param);
  if (index === -1) return;

  editingCustomParamIndex.value = index;
  isIdTouched.value = true;
  customParamForm.id = param.id;
  customParamForm.name = param.name;
  customParamForm.target = param.target;
  customParamForm.newValue = param.newValue;
  customParamError.value = null;
  isCustomParamModalOpen.value = true;
}

function onCustomNameInput(val: string) {
  if (!isIdTouched.value && editingCustomParamIndex.value === null) {
    const slug = val
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, '_')
      .replace(/^([0-9])/, '_$1');
    customParamForm.id = slug ? `custom_${slug}` : '';
  }
}

function applyXPathPreset(template: string) {
  customParamForm.target = template;
}

function saveCustomParam() {
  const trimmedId = customParamForm.id.trim();
  const trimmedName = customParamForm.name.trim();
  const trimmedTarget = customParamForm.target.trim();
  const trimmedVal = customParamForm.newValue.trim();

  if (!trimmedId) {
    customParamError.value = 'Parameter ID cannot be empty.';
    return;
  }
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmedId)) {
    customParamError.value = 'Parameter ID must be a valid SId (letters, digits, underscores, starting with letter/underscore).';
    return;
  }
  if (!trimmedTarget) {
    customParamError.value = 'Target XPath expression cannot be empty.';
    return;
  }
  if (!trimmedVal) {
    customParamError.value = 'Perturbation value cannot be empty.';
    return;
  }

  const isEditing = editingCustomParamIndex.value !== null;
  const duplicate = introspectedParameters.value.some((p, idx) => {
    if (isEditing && idx === editingCustomParamIndex.value) return false;
    return p.id.toLowerCase() === trimmedId.toLowerCase();
  });

  if (duplicate) {
    customParamError.value = `A parameter with ID "${trimmedId}" already exists.`;
    return;
  }

  if (isEditing) {
    const existing = introspectedParameters.value[editingCustomParamIndex.value!];
    if (existing) {
      existing.id = trimmedId;
      existing.name = trimmedName || trimmedId;
      existing.target = trimmedTarget;
      existing.newValue = trimmedVal;
    }
  } else {
    introspectedParameters.value.push({
      id: trimmedId,
      name: trimmedName || trimmedId,
      target: trimmedTarget,
      defaultValue: '',
      newValue: trimmedVal,
      isCustom: true
    });
  }

  isCustomParamModalOpen.value = false;
  toast.add({
    title: isEditing ? 'Custom Parameter Updated' : 'Custom Parameter Added',
    description: `Parameter "${trimmedName || trimmedId}" is configured.`,
    color: 'success',
    icon: 'i-lucide-check'
  });
}

function removeCustomParameter(param: EditableParameter) {
  const index = introspectedParameters.value.indexOf(param);
  if (index !== -1) {
    introspectedParameters.value.splice(index, 1);
    toast.add({
      title: 'Custom Parameter Removed',
      description: `Removed "${param.name || param.id}".`,
      color: 'neutral',
      icon: 'i-lucide-trash-2'
    });
  }
}

function deleteFromModal() {
  if (editingCustomParamIndex.value !== null) {
    const existing = introspectedParameters.value[editingCustomParamIndex.value];
    if (existing) {
      removeCustomParameter(existing);
    }
    isCustomParamModalOpen.value = false;
  }
}

const parameterColumns: TableColumn<EditableParameter>[] = [
  {
    accessorKey: 'id',
    header: 'Parameter ID',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'whitespace-nowrap font-mono font-medium text-neutral-900'
      }
    }
  },
  {
    accessorKey: 'name',
    header: 'Name',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'whitespace-nowrap font-medium text-neutral-800'
      }
    }
  },
  {
    accessorKey: 'defaultValue',
    header: 'Default',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'whitespace-nowrap font-mono text-neutral-500'
      }
    }
  },
  {
    accessorKey: 'newValue',
    header: 'New Value',
    meta: {
      class: {
        th: 'whitespace-nowrap w-52 min-w-[13rem]',
        td: 'whitespace-nowrap w-52 min-w-[13rem]'
      }
    }
  },
  {
    accessorKey: 'target',
    header: 'Target',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'max-w-xs sm:max-w-md'
      }
    }
  }
];

const parameterTableMeta: TableMeta<EditableParameter> = {
  class: {
    tr: (row: Row<EditableParameter>) => (row.original.newValue !== row.original.defaultValue ? 'bg-primary/5' : '')
  }
};

const observableColumns: TableColumn<SelectableVariable>[] = [
  {
    id: 'enabled',
    header: 'Enabled',
    meta: {
      class: {
        th: 'w-16 text-center whitespace-nowrap',
        td: 'w-16 text-center whitespace-nowrap'
      }
    }
  },
  {
    accessorKey: 'id',
    header: 'Variable ID',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'whitespace-nowrap font-mono font-medium text-neutral-900'
      }
    }
  },
  {
    accessorKey: 'name',
    header: 'Name',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'text-neutral-700 whitespace-nowrap'
      }
    }
  },
  {
    id: 'target',
    header: 'Symbol / XPath Target',
    meta: {
      class: {
        th: 'whitespace-nowrap',
        td: 'font-mono text-[11px] text-neutral-500 max-w-sm'
      }
    }
  }
];

const observableTableMeta: TableMeta<SelectableVariable> = {
  class: {
    tr: (row: Row<SelectableVariable>) => (row.original.selected ? 'hover:bg-neutral-50/80 transition-colors' : 'opacity-60 bg-neutral-50/40 hover:bg-neutral-50/80 transition-colors')
  }
};

const canAdvanceStep4 = computed(() => {
  return selectedVariablesCount.value > 0;
});

// Introspection Runner
async function runIntrospection() {
  isIntrospecting.value = true;
  introspectionError.value = null;

  const formData = new FormData();
  if (inputMode.value === 'file' && selectedFile.value) {
    formData.append('modelFile', selectedFile.value, selectedFile.value.name);
  } else if (modelUrl.value.trim()) {
    formData.append('modelUrl', modelUrl.value.trim());
  } else {
    isIntrospecting.value = false;
    return;
  }

  formData.append('modelLanguage', selectedLanguage.value.sedUrn);
  formData.append('modelingFramework', selectedFrameworkId.value);
  formData.append('simulationType', selectedSimType.value);
  formData.append('simulationAlgorithm', selectedAlgorithmId.value.trim());

  try {
    const sedDoc: SedDocument = await introspectModel(formData);

    // 1. Gather Namespaces
    const namespaces: Namespace[] = [];
    const extractNamespaces = (list?: Namespace[]) => {
      if (!list) return;
      for (const ns of list) {
        if (!namespaces.some(n => n.prefix === ns.prefix)) {
          namespaces.push(ns);
        }
      }
    };

    // 2. Gather Parameters (Model Changes)
    const paramsList: EditableParameter[] = [];
    const rawChanges = sedDoc.models?.[0]?.changes || [];
    for (const c of rawChanges) {
      extractNamespaces(c.target?.namespaces);
      paramsList.push({
        id: c.id,
        name: c.name || c.id,
        target: c.target?.value || '',
        defaultValue: c.newValue || c.default || '',
        newValue: c.newValue || c.default || ''
      });
    }
    introspectedParameters.value = paramsList;

    // 3. Gather Observables (from dataGenerators)
    const varsList: SelectableVariable[] = [];
    const seenIds = new Set<string>();

    const generators = sedDoc.dataGenerators || [];
    for (const gen of generators) {
      for (const v of gen.variables || []) {
        if (!seenIds.has(v.id)) {
          seenIds.add(v.id);
          extractNamespaces(v.target?.namespaces);
          varsList.push({
            id: v.id,
            name: v.name || gen.name || v.id,
            symbol: v.symbol,
            target: v.target?.value,
            selected: true
          });
        }
      }
    }

    // Fallback: If no data generators returned, seed time variable
    if (varsList.length === 0 && selectedSimType.value === 'SedUniformTimeCourseSimulation') {
      varsList.push({
        id: 'time',
        name: 'Time',
        symbol: 'urn:sedml:symbol:time',
        selected: true
      });
    }

    observableVariables.value = varsList;
    introspectedNamespaces.value = namespaces;

    // 4. Time course defaults if available
    const introspectedSim = sedDoc.simulations?.[0];
    if (introspectedSim && introspectedSim._type === 'SedUniformTimeCourseSimulation') {
      const tc = introspectedSim as SedUniformTimeCourseSimulation;
      if (typeof tc.initialTime === 'number') timeCourse.initialTime = tc.initialTime;
      if (typeof tc.outputStartTime === 'number') timeCourse.outputStartTime = tc.outputStartTime;
      if (typeof tc.outputEndTime === 'number') timeCourse.outputEndTime = tc.outputEndTime;
      if (typeof tc.numberOfSteps === 'number') timeCourse.numberOfSteps = tc.numberOfSteps;
    }

    introspectionDone.value = true;
    toast.add({
      title: 'Model Introspection Complete',
      description: `Identified ${paramsList.length} parameter(s) and ${varsList.length} observable variable(s).`,
      color: 'success',
      icon: 'i-lucide-check'
    });
  } catch (err: any) {
    introspectionError.value = err?.message || 'Introspection service could not parse model structure.';
    // Provide fallback baseline time variable if empty
    if (observableVariables.value.length === 0) {
      observableVariables.value = [
        { id: 'time', name: 'Time', symbol: 'urn:sedml:symbol:time', selected: true }
      ];
    }
  } finally {
    isIntrospecting.value = false;
  }
}

// =============================================================================
// Step 5: Build & Export COMBINE Archive (.omex)
// =============================================================================

const isCompiling = ref(false);
const compilationSuccess = ref(false);
const createdBlob = ref<Blob | null>(null);
const createdBlobSize = ref<number>(0);

const suggestedSimulator = computed(() => {
  const lang = selectedLanguage.value.id;
  const alg = selectedAlgorithmId.value;
  if (lang === 'SBML') {
    if (alg.includes('0000560')) return 'cobrapy';
    return 'copasi';
  }
  if (lang === 'CellML') return 'opencor';
  if (lang === 'Smoldyn') return 'smoldyn';
  if (lang === 'BNGL') return 'bionetgen';
  if (lang === 'GINsim') return 'ginsim';
  return 'copasi';
});

const runSimulationUrl = computed(() => {
  const params = new URLSearchParams();
  if (archiveName.value) params.append('runName', archiveName.value);
  if (suggestedSimulator.value) params.append('simulator', suggestedSimulator.value);
  return `/simulations/run?${params.toString()}`;
});

function assembleCombineArchive(): { archive: CombineArchive; file?: File } {
  const selectedLang = selectedLanguage.value;
  const simType = selectedSimType.value;
  const algId = selectedAlgorithmId.value.trim();

  const fileExt = selectedFile.value
    ? '.' + (selectedFile.value.name.split('.').pop() || 'xml')
    : selectedLang.extensions[0] || '.xml';
  const modelArchiveFilename = `model${fileExt}`;

  // 1. Model Changes
  const appliedChanges: SedModelChange[] = activeParameterChanges.value.map(c => ({
    _type: 'SedModelAttributeChange',
    id: c.id,
    name: c.name || c.id,
    newValue: String(c.newValue),
    target: {
      _type: 'SedTarget',
      value: c.target,
      namespaces: effectiveNamespaces.value
    }
  }));

  // 2. Model
  const model: SedModel = {
    _type: 'SedModel',
    id: 'model_1',
    language: selectedLang.sedUrn,
    source: modelArchiveFilename,
    changes: appliedChanges.length > 0 ? appliedChanges : []
  };

  // 3. Algorithm
  const algorithm = {
    _type: 'SedAlgorithm' as const,
    kisaoId: algId,
    changes: []
  };

  // 4. Simulation
  let simulation: SedSimulation;
  if (simType === 'SedSteadyStateSimulation') {
    simulation = {
      _type: 'SedSteadyStateSimulation',
      id: 'simulation_1',
      algorithm
    };
  } else if (simType === 'SedOneStepSimulation') {
    simulation = {
      _type: 'SedOneStepSimulation',
      id: 'simulation_1',
      step: Number(stepSize.value) || 0.1,
      algorithm
    };
  } else {
    simulation = {
      _type: 'SedUniformTimeCourseSimulation',
      id: 'simulation_1',
      initialTime: Number(timeCourse.initialTime) || 0,
      outputStartTime: Number(timeCourse.outputStartTime) || 0,
      outputEndTime: Number(timeCourse.outputEndTime) || 10,
      numberOfSteps: Number(timeCourse.numberOfSteps) || 100,
      algorithm
    };
  }

  // 5. Task
  const task: SedTask = {
    _type: 'SedTask',
    id: 'task_1',
    model: model.id,
    simulation: simulation.id
  };

  // 6. Data Generators & DataSets
  const selectedVars = observableVariables.value.filter(v => v.selected);
  const dataGenerators: SedDataGenerator[] = [];
  const dataSets: SedDataSet[] = [];

  for (const v of selectedVars) {
    const rawId = v.id;
    const genId = `data_generator_${rawId}`;
    const setId = `data_set_${rawId}`;

    const sedVar: SedVariable = {
      _type: 'SedVariable',
      id: `variable_${rawId}`,
      name: v.name || rawId,
      symbol: v.symbol || undefined,
      target: !v.symbol && v.target
? {
        _type: 'SedTarget',
        value: v.target,
        namespaces: introspectedNamespaces.value
      }
: undefined,
      model: model.id,
      task: task.id
    };

    dataGenerators.push({
      _type: 'SedDataGenerator',
      id: genId,
      name: v.name || rawId,
      math: sedVar.id,
      parameters: [],
      variables: [sedVar]
    });

    dataSets.push({
      _type: 'SedDataSet',
      id: setId,
      label: v.name || rawId,
      name: v.name || rawId,
      dataGenerator: genId
    });
  }

  // 7. Outputs: Report and optional 2D Plot
  const outputs: any[] = [
    {
      _type: 'SedReport',
      id: 'report_1',
      name: 'Simulation Report',
      dataSets
    }
  ];

  const timeVar = selectedVars.find(
    v => v.id.toLowerCase() === 'time' || (v.symbol && v.symbol.toLowerCase().includes('time'))
  );
  if (timeVar && dataSets.length > 1) {
    const timeGenId = `data_generator_${timeVar.id}`;
    const curves: SedCurve[] = dataSets
      .filter(ds => ds.id !== `data_set_${timeVar.id}`)
      .map(ds => ({
        _type: 'SedCurve',
        id: `curve_${ds.id}`,
        name: ds.label,
        xDataGenerator: timeGenId,
        yDataGenerator: ds.dataGenerator
      }));

    if (curves.length > 0) {
      outputs.push({
        _type: 'SedPlot2D',
        id: 'plot_1',
        name: 'Simulation Dynamics',
        curves,
        xScale: 'linear',
        yScale: 'linear'
      });
    }
  }

  // 8. SED-ML Document
  const sedDoc: SedDocument = {
    _type: 'SedDocument',
    level: 1,
    version: 3,
    styles: [],
    models: [model],
    simulations: [simulation],
    tasks: [task],
    dataGenerators,
    outputs
  };

  // 9. Combine Archive
  const archiveLocationValue = selectedFile.value
    ? {
        _type: 'CombineArchiveContentFile' as const,
        filename: selectedFile.value.name
      }
    : {
        _type: 'CombineArchiveContentUrl' as const,
        url: modelUrl.value.trim()
      };

  const archive: CombineArchive = {
    _type: 'CombineArchive',
    contents: [
      {
        _type: 'CombineArchiveContent',
        format: selectedLang.omexManifestUri,
        master: false,
        location: {
          _type: 'CombineArchiveLocation',
          path: modelArchiveFilename,
          value: archiveLocationValue
        }
      },
      {
        _type: 'CombineArchiveContent',
        format: 'http://identifiers.org/combine.specifications/sed-ml',
        master: true,
        location: {
          _type: 'CombineArchiveLocation',
          path: 'simulation.sedml',
          value: sedDoc
        }
      }
    ]
  };

  return {
    archive,
    file: selectedFile.value || undefined
  };
}

function triggerDownloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}

async function handleCompileAndDownload() {
  isCompiling.value = true;
  compilationSuccess.value = false;

  try {
    const { archive, file } = assembleCombineArchive();
    const blob = await createCombineArchive(archive, file);

    createdBlob.value = blob;
    createdBlobSize.value = blob.size;
    compilationSuccess.value = true;

    const safeFilename = `${archiveName.value || 'simulation-experiment'}.omex`;
    triggerDownloadBlob(blob, safeFilename);

    toast.add({
      title: 'COMBINE Archive Created',
      description: `${safeFilename} (${(blob.size / 1024).toFixed(1)} KB) has been downloaded.`,
      color: 'success',
      icon: 'i-lucide-check-circle-2'
    });
  } catch (err: any) {
    toast.add({
      title: 'Archive Creation Failed',
      description: err?.message || 'Failed to assemble COMBINE archive.',
      color: 'error',
      icon: 'i-lucide-alert-triangle'
    });
  } finally {
    isCompiling.value = false;
  }
}

function downloadAgain() {
  if (createdBlob.value) {
    const safeFilename = `${archiveName.value || 'simulation-experiment'}.omex`;
    triggerDownloadBlob(createdBlob.value, safeFilename);
  }
}

function resetWizard() {
  activeStep.value = 0;
  selectedFile.value = null;
  modelUrl.value = '';
  archiveName.value = 'simulation-experiment';
  introspectedParameters.value = [];
  observableVariables.value = [];
  introspectionDone.value = false;
  introspectionError.value = null;
  compilationSuccess.value = false;
  createdBlob.value = null;
}

// Stepper Navigation
function goToStep(index: number) {
  activeStep.value = index;
  if (index === 2 && !introspectionDone.value && !isIntrospecting.value) {
    runIntrospection();
  }
}

// Pre-load from query parameters
onMounted(() => {
  if (route.query.modelUrl) {
    modelUrl.value = String(route.query.modelUrl);
    inputMode.value = 'url';
  }
  if (route.query.runName) {
    archiveName.value = sanitizeArchiveName(String(route.query.runName));
  }
  if (route.query.modelFormat) {
    const fmt = String(route.query.modelFormat).toUpperCase();
    const matched = MODEL_LANGUAGE_OPTIONS.find(l => l.id.toUpperCase() === fmt);
    if (matched) selectedLanguage.value = matched;
  }
  if (route.query.modelingFramework) {
    const fw = String(route.query.modelingFramework).toUpperCase();
    const matched = MODELING_FRAMEWORKS.find(f => f.id.toUpperCase() === fw);
    if (matched) selectedFrameworkId.value = matched.id;
  }
  if (route.query.simulationType) {
    const st = String(route.query.simulationType);
    const matched = SIMULATION_TYPES.find(t => t.id.toLowerCase() === st.toLowerCase());
    if (matched) selectedSimType.value = matched.id;
  }
  if (route.query.simulationAlgorithm) {
    const alg = String(route.query.simulationAlgorithm);
    selectedAlgorithmId.value = alg;
  }
});
</script>

<template>
  <div class="min-h-screen bg-neutral-50 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumbs & Header -->
      <div>
        <UBreadcrumb :items="breadcrumbs" class="mb-3" />

        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-3xl font-bold tracking-tight text-neutral-900 flex items-center gap-3">
              <UIcon name="i-lucide-flask-conical" class="size-8 text-primary" />
              New Simulation Experiment
            </h1>
            <p class="mt-1 text-sm text-neutral-600 max-w-3xl">
              Design SED-ML simulation experiments, introspect model parameters and observable variables, adjust time course conditions, and compile a standardized COMBINE archive (.omex).
            </p>
          </div>

          <div class="flex items-center gap-2">
            <UButton
              to="https://docs.biosimulations.org/users/creating-projects/"
              target="_blank"
              rel="noopener noreferrer"
              color="neutral"
              variant="outline"
              icon="i-lucide-external-link"
            >
              Experiment Guide
            </UButton>
          </div>
        </div>
      </div>

      <!-- Main Stepper Card -->
      <UCard class="w-full shadow-sm border border-neutral-200 bg-white">
        <!-- Nuxt UI Stepper Header -->
        <UStepper
          ref="stepper"
          v-model="activeStep"
          :items="steps"
          class="w-full mb-8 pb-6 border-b border-neutral-200"
        />

        <!-- STEP 1: MODEL SELECTION -->
        <div v-if="activeStep === 0" class="space-y-6">
          <div class="border-b border-neutral-100 pb-4">
            <h2 class="text-lg font-semibold text-neutral-900 flex items-center gap-2">
              <UIcon name="i-lucide-file-code" class="size-5 text-primary" />
              Model Source & Format
            </h2>
            <p class="text-xs text-neutral-500 mt-1">
              Upload your computational model file or specify a public download URL to begin.
            </p>
          </div>

          <!-- Archive Name & Format -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="space-y-1.5">
              <label for="archive-name-input" class="block text-sm font-medium text-neutral-700">
                Archive Filename
              </label>
              <div class="relative">
                <UInput
                  id="archive-name-input"
                  v-model="archiveName"
                  placeholder="simulation-experiment"
                  icon="i-lucide-archive"
                  class="w-full"
                />
              </div>
              <p class="text-xs text-neutral-500">
                Will be compiled as <span class="font-mono text-neutral-700 font-medium">{{ archiveName || 'simulation-experiment' }}.omex</span>
              </p>
            </div>

            <div class="space-y-1.5">
              <label for="model-language-select" class="block text-sm font-medium text-neutral-700">
                Model Language
              </label>
              <USelectMenu
                id="model-language-select"
                v-model="selectedLanguage"
                data-lenis-prevent
                :items="MODEL_LANGUAGE_OPTIONS"
                label-key="name"
                class="w-full"
              />
              <p class="text-xs text-neutral-500">
                Supported: {{ selectedLanguage.extensions.join(', ') }}
              </p>
            </div>
          </div>

          <!-- Input Method Switcher -->
          <div class="space-y-3 pt-2">
            <label class="block text-xs font-semibold text-neutral-600 uppercase tracking-wider">
              Model Input Method
            </label>
            <UTabs v-model="inputMode" :items="inputModes" class="w-full" />
          </div>

          <!-- File Upload Mode -->
          <div v-if="inputMode === 'file'" class="pt-1">
            <UFileUpload
              v-model="selectedFile"
              :accept="selectedLanguage.accept"
              layout="list"
              icon="i-lucide-upload-cloud"
              label="Drop model file here"
              class="w-full min-h-36"
            >
              <template #description>
                <div class="flex flex-col items-center gap-1 mt-1">
                  <span class="text-xs text-neutral-500">or click to browse from your device</span>
                  <div class="flex flex-wrap items-center justify-center gap-1.5 mt-1">
                    <span class="text-xs text-neutral-400">Accepted formats:</span>
                    <UBadge
                      v-for="ext in selectedLanguage.extensions"
                      :key="ext"
                      size="sm"
                      variant="subtle"
                      color="neutral"
                    >
                      {{ ext }}
                    </UBadge>
                  </div>
                </div>
              </template>
            </UFileUpload>

            <div v-if="selectedFile" class="mt-3 p-3 bg-neutral-50 rounded-lg border border-neutral-200 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <UIcon name="i-lucide-file" class="size-4 text-primary" />
                <span class="text-sm font-medium text-neutral-800">{{ selectedFile.name }}</span>
                <span class="text-xs text-neutral-500">({{ (selectedFile.size / 1024).toFixed(1) }} KB)</span>
              </div>
              <UBadge size="sm" color="success" variant="subtle">Selected</UBadge>
            </div>
          </div>

          <!-- URL Input Mode -->
          <div v-else class="space-y-2 pt-1">
            <label for="model-url-input" class="block text-sm font-medium text-neutral-700">
              Public Model File URL
            </label>
            <UInput
              id="model-url-input"
              v-model="modelUrl"
              placeholder="https://example.org/path/to/model.xml"
              icon="i-lucide-globe"
              class="w-full"
            />
            <p v-if="urlError" class="text-xs text-error font-medium">
              {{ urlError }}
            </p>
            <p v-else class="text-xs text-neutral-500">
              Provide a direct, publicly accessible download link to your model file.
            </p>
          </div>

          <!-- Navigation Footer -->
          <div class="flex items-center justify-end pt-6 border-t border-neutral-100">
            <UButton
              color="primary"
              trailing-icon="i-lucide-arrow-right"
              :disabled="!canAdvanceStep1"
              @click="goToStep(1)"
            >
              Continue to Simulation Method
            </UButton>
          </div>
        </div>

        <!-- STEP 2: SIMULATION METHOD -->
        <div v-else-if="activeStep === 1" class="space-y-6">
          <div class="border-b border-neutral-100 pb-4">
            <h2 class="text-lg font-semibold text-neutral-900 flex items-center gap-2">
              <UIcon name="i-lucide-cpu" class="size-5 text-primary" />
              Modeling Framework & Algorithm
            </h2>
            <p class="text-xs text-neutral-500 mt-1">
              Select the mathematical framework, simulation type, and execution algorithm for your experiment.
            </p>
          </div>

          <!-- Modeling Framework -->
          <div class="space-y-2">
            <label for="framework-select" class="block text-sm font-medium text-neutral-700">
              Modeling Framework (SBO)
            </label>
            <USelect
              id="framework-select"
              v-model="selectedFrameworkId"
              data-lenis-prevent
              :items="MODELING_FRAMEWORKS"
              label-key="name"
              value-key="id"
              class="w-full"
            />
            <p class="text-xs text-neutral-500">
              {{ currentFramework.description }}
            </p>
          </div>

          <!-- Simulation Type -->
          <div class="space-y-2 pt-2">
            <label class="block text-sm font-medium text-neutral-700">
              Simulation Type
            </label>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div
                v-for="st in SIMULATION_TYPES"
                :key="st.id"
                class="p-4 rounded-lg border cursor-pointer transition-all flex flex-col justify-between"
                :class="selectedSimType === st.id ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'border-neutral-200 bg-white hover:border-neutral-300'"
                @click="selectedSimType = st.id"
              >
                <div>
                  <div class="flex items-center gap-2 mb-1.5">
                    <UIcon :name="st.icon" class="size-4" :class="selectedSimType === st.id ? 'text-primary' : 'text-neutral-500'" />
                    <span class="text-sm font-semibold text-neutral-900">{{ st.name }}</span>
                  </div>
                  <p class="text-xs text-neutral-500 leading-relaxed">
                    {{ st.description }}
                  </p>
                </div>
                <div class="mt-3">
                  <UBadge
                    v-if="selectedSimType === st.id"
                    size="sm"
                    color="primary"
                    variant="subtle"
                  >
                    Active
                  </UBadge>
                </div>
              </div>
            </div>
          </div>

          <!-- Simulation Algorithm -->
          <div class="space-y-2 pt-2">
            <label for="algorithm-select" class="block text-sm font-medium text-neutral-700">
              Simulation Algorithm (KiSAO)
            </label>
            <USelect
              id="algorithm-select"
              v-model="selectedAlgorithmId"
              data-lenis-prevent
              :items="relevantAlgorithms.length > 0 ? relevantAlgorithms : SIMULATION_ALGORITHMS"
              label-key="name"
              value-key="id"
              class="w-full"
            />
            <div class="flex items-center justify-between text-xs text-neutral-500 pt-1">
              <span>Selected ID: <code class="font-mono text-neutral-700 font-semibold">{{ selectedAlgorithmId }}</code></span>
              <button
                type="button"
                class="text-primary hover:underline text-xs"
                @click="customAlgorithmInput = customAlgorithmInput ? '' : 'KISAO_'"
              >
                {{ customAlgorithmInput ? 'Use presets' : 'Enter custom KiSAO ID' }}
              </button>
            </div>

            <!-- Custom KiSAO input toggle -->
            <div v-if="customAlgorithmInput !== ''" class="pt-2">
              <UInput
                v-model="selectedAlgorithmId"
                placeholder="KISAO_0000019"
                icon="i-lucide-hash"
                class="w-full"
              />
            </div>
          </div>

          <!-- Navigation Footer -->
          <div class="flex items-center justify-between pt-6 border-t border-neutral-100">
            <UButton
              color="neutral"
              variant="outline"
              leading-icon="i-lucide-arrow-left"
              @click="goToStep(0)"
            >
              Back
            </UButton>
            <UButton
              color="primary"
              trailing-icon="i-lucide-arrow-right"
              :disabled="!canAdvanceStep2"
              @click="goToStep(2)"
            >
              Continue to Experiment Setup
            </UButton>
          </div>
        </div>

        <!-- STEP 3: EXPERIMENT SETUP & INTROSPECTION -->
        <div v-else-if="activeStep === 2" class="space-y-6">
          <div class="border-b border-neutral-100 pb-4">
            <h2 class="text-lg font-semibold text-neutral-900 flex items-center gap-2">
              <UIcon name="i-lucide-sliders" class="size-5 text-primary" />
              Experiment Setup & Model Introspection
            </h2>
            <p class="text-xs text-neutral-500 mt-1">
              Configure simulation duration, intervals, and review introspected model properties.
            </p>
          </div>

          <!-- Loading State for Introspection -->
          <div v-if="isIntrospecting" class="py-12 flex flex-col items-center justify-center text-center space-y-4">
            <UIcon name="i-lucide-loader-2" class="size-10 text-primary animate-spin" />
            <div>
              <p class="text-sm font-semibold text-neutral-900">Introspecting Model Structure...</p>
              <p class="text-xs text-neutral-500 mt-1">
                Extracting model parameters, rate laws, and observable species via COMBINE API.
              </p>
            </div>
          </div>

          <!-- Introspection Error Banner -->
          <div v-else-if="introspectionError" class="space-y-4">
            <UAlert
              color="error"
              variant="subtle"
              icon="i-lucide-alert-circle"
              title="Introspection Notice"
              :description="introspectionError"
            />
            <div class="flex items-center gap-3">
              <UButton
                color="neutral"
                variant="outline"
                size="sm"
                icon="i-lucide-refresh-cw"
                @click="runIntrospection"
              >
                Retry Introspection
              </UButton>
              <span class="text-xs text-neutral-500">
                You can continue to configure experiment duration and variables manually.
              </span>
            </div>
          </div>

          <!-- Introspection Success Banner -->
          <div v-else-if="introspectionDone" class="p-3 bg-success/5 border border-success/20 rounded-lg flex items-center justify-between">
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-check-circle" class="size-5 text-success" />
              <div>
                <span class="text-sm font-medium text-neutral-900">Model Introspected Successfully</span>
                <p class="text-xs text-neutral-500">
                  Extracted {{ introspectedParameters.length }} parameter(s) and {{ observableVariables.length }} observable variable(s).
                </p>
              </div>
            </div>
            <UButton
              color="neutral"
              variant="ghost"
              size="xs"
              icon="i-lucide-refresh-cw"
              @click="runIntrospection"
            >
              Re-scan
            </UButton>
          </div>

          <!-- Time Course Parameters Form (if Uniform Time Course) -->
          <div v-if="selectedSimType === 'SedUniformTimeCourseSimulation'" class="space-y-4 pt-2">
            <h3 class="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <UIcon name="i-lucide-clock" class="size-4 text-primary" />
              Uniform Time Course Duration & Sampling
            </h3>

            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div class="space-y-1">
                <label for="tc-initial-time" class="block text-xs font-medium text-neutral-700">Initial Time</label>
                <UInput
                  id="tc-initial-time"
                  v-model.number="timeCourse.initialTime"
                  type="number"
                  step="any"
                  class="w-full"
                />
              </div>

              <div class="space-y-1">
                <label for="tc-output-start" class="block text-xs font-medium text-neutral-700">Output Start Time</label>
                <UInput
                  id="tc-output-start"
                  v-model.number="timeCourse.outputStartTime"
                  type="number"
                  step="any"
                  class="w-full"
                />
              </div>

              <div class="space-y-1">
                <label for="tc-output-end" class="block text-xs font-medium text-neutral-700">Output End Time</label>
                <UInput
                  id="tc-output-end"
                  v-model.number="timeCourse.outputEndTime"
                  type="number"
                  step="any"
                  class="w-full"
                />
              </div>

              <div class="space-y-1">
                <label for="tc-steps" class="block text-xs font-medium text-neutral-700">Number of Steps</label>
                <UInput
                  id="tc-steps"
                  v-model.number="timeCourse.numberOfSteps"
                  type="number"
                  min="1"
                  step="1"
                  class="w-full"
                />
              </div>
            </div>

            <!-- Derived Metrics -->
            <div class="p-3 bg-neutral-50 rounded-lg border border-neutral-200 grid grid-cols-2 sm:grid-cols-3 gap-4 text-xs">
              <div>
                <span class="text-neutral-500 block">Step Size (Δt):</span>
                <span class="font-mono font-semibold text-neutral-900">{{ stepSize }}</span>
              </div>
              <div>
                <span class="text-neutral-500 block">Time Points:</span>
                <span class="font-semibold text-neutral-900">{{ timeCourse.numberOfSteps + 1 }}</span>
              </div>
              <div>
                <span class="text-neutral-500 block">Time Range:</span>
                <span class="font-mono text-neutral-900">[{{ timeCourse.outputStartTime }}, {{ timeCourse.outputEndTime }}]</span>
              </div>
            </div>
          </div>

          <!-- Steady State / One Step Info -->
          <div v-else class="p-4 bg-neutral-50 rounded-lg border border-neutral-200">
            <p class="text-sm font-medium text-neutral-900">
              {{ currentSimType.name }} Configuration
            </p>
            <p class="text-xs text-neutral-500 mt-1">
              {{ currentSimType.description }} No time duration sampling required for this simulation mode.
            </p>
          </div>

          <!-- Navigation Footer -->
          <div class="flex items-center justify-between pt-6 border-t border-neutral-100">
            <UButton
              color="neutral"
              variant="outline"
              leading-icon="i-lucide-arrow-left"
              @click="goToStep(1)"
            >
              Back
            </UButton>
            <UButton
              color="primary"
              trailing-icon="i-lucide-arrow-right"
              :disabled="!canAdvanceStep3 || isIntrospecting"
              @click="goToStep(3)"
            >
              Continue to Customization
            </UButton>
          </div>
        </div>

        <!-- STEP 4: CUSTOMIZATION (PARAMETERS & OBSERVABLES) -->
        <div v-else-if="activeStep === 3" class="space-y-8">
          <div class="border-b border-neutral-100 pb-4">
            <h2 class="text-lg font-semibold text-neutral-900 flex items-center gap-2">
              <UIcon name="i-lucide-list-checks" class="size-5 text-primary" />
              Customize Parameters & Observables
            </h2>
            <p class="text-xs text-neutral-500 mt-1">
              Apply parameter changes to model kinetics and select observable variables for output reports and plots.
            </p>
          </div>

          <!-- Subsection 1: Model Parameters / Changes -->
          <div class="space-y-4">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-semibold text-neutral-900 flex items-center gap-2">
                  <UIcon name="i-lucide-sliders-horizontal" class="size-4 text-primary" />
                  Model Parameters (SED-ML Changes)
                </h3>
                <p class="text-xs text-neutral-500">
                  Override initial quantities, compartment volumes, or rate parameters.
                </p>
              </div>

              <div class="flex items-center gap-2">
                <UBadge size="sm" variant="subtle" color="neutral">
                  {{ activeParameterChanges.length }} modified
                </UBadge>
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  icon="i-lucide-plus"
                  @click="openAddCustomParamModal"
                >
                  Add Custom Parameter
                </UButton>
              </div>
            </div>

            <!-- Parameter Search Filter -->
            <div v-if="introspectedParameters.length > 5">
              <UInput
                v-model="parameterSearch"
                placeholder="Filter parameters by ID or name..."
                icon="i-lucide-search"
                class="w-full sm:w-80"
                size="sm"
              />
            </div>

            <!-- Parameters Table -->
            <div v-if="introspectedParameters.length > 0" class="border border-neutral-200 rounded-lg overflow-hidden bg-white">
              <UTable
                :data="filteredParameters"
                :columns="parameterColumns"
                :meta="parameterTableMeta"
                :ui="{
                  th: 'py-2.5 px-3 text-xs font-semibold text-neutral-700 bg-neutral-100 border-b border-neutral-200 whitespace-nowrap',
                  td: 'py-2 px-3 text-xs whitespace-nowrap'
                }"
                sticky
                class="max-h-72 overflow-auto lenis-prevent"
                data-lenis-prevent
                empty="No parameters match your search."
              >
                <template #id-cell="{ row }">
                  <div class="flex items-center gap-1 whitespace-nowrap">
                    <template v-if="row.original.isCustom">
                      <UButton
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        icon="i-lucide-pencil"
                        title="Edit custom parameter details"
                        aria-label="Edit custom parameter details"
                        @click.stop="openEditCustomParamModal(row.original)"
                      />
                      <UButton
                        size="xs"
                        color="error"
                        variant="ghost"
                        icon="i-lucide-trash-2"
                        title="Delete custom parameter"
                        aria-label="Delete custom parameter"
                        @click.stop="removeCustomParameter(row.original)"
                      />
                    </template>
                    <span class="font-mono font-medium text-neutral-900">{{ row.original.id }}</span>
                  </div>
                </template>

                <template #name-cell="{ row }">
                  <div class="flex items-center gap-1.5 whitespace-nowrap">
                    <span class="font-medium text-neutral-800">{{ row.original.name }}</span>
                    <UBadge
                      v-if="row.original.isCustom"
                      size="xs"
                      color="primary"
                      variant="subtle"
                      class="text-[10px] px-1.5 py-0"
                    >
                      Custom
                    </UBadge>
                  </div>
                </template>

                <template #defaultValue-cell="{ row }">
                  <span class="whitespace-nowrap font-mono text-neutral-500">{{ row.original.defaultValue || '—' }}</span>
                </template>

                <template #newValue-cell="{ row }">
                  <div class="whitespace-nowrap">
                    <UInput
                      v-model="row.original.newValue"
                      size="xs"
                      placeholder="Unchanged"
                      class="w-48 font-mono"
                    />
                  </div>
                </template>

                <template #target-cell="{ row }">
                  <UTooltip
                    v-if="row.original.target"
                    :text="row.original.target"
                    :delay-duration="0"
                    :content="{ side: 'top', align: 'start' }"
                  >
                    <span class="block max-w-xs sm:max-w-md truncate font-mono text-[11px] text-neutral-500 cursor-help">
                      {{ row.original.target }}
                    </span>
                  </UTooltip>
                  <span v-else class="text-neutral-400 font-mono text-[11px]">—</span>
                </template>
              </UTable>
            </div>

            <div v-else class="p-6 text-center border border-dashed border-neutral-300 rounded-lg text-neutral-500 text-xs">
              <p>No parameters found in introspected model.</p>
              <div class="mt-2">
                <UButton
                  size="xs"
                  color="primary"
                  variant="soft"
                  icon="i-lucide-plus"
                  @click="openAddCustomParamModal"
                >
                  Add Custom Parameter
                </UButton>
              </div>
            </div>
          </div>

          <!-- Subsection 2: Observables / Output Variables -->
          <div class="space-y-4 pt-4 border-t border-neutral-200">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-semibold text-neutral-900 flex items-center gap-2">
                  <UIcon name="i-lucide-activity" class="size-4 text-primary" />
                  Observable Variables (Outputs & Plots)
                </h3>
                <p class="text-xs text-neutral-500">
                  Select which state variables and species will be recorded in SED-ML reports and 2D charts.
                </p>
              </div>

              <div class="flex items-center gap-2">
                <UBadge size="sm" variant="subtle" :color="selectedVariablesCount > 0 ? 'primary' : 'error'">
                  {{ selectedVariablesCount }} of {{ observableVariables.length }} enabled
                </UBadge>
                <UButton size="xs" color="neutral" variant="outline" @click="selectAllVariables">
                  Enable All
                </UButton>
                <UButton size="xs" color="neutral" variant="outline" @click="deselectAllVariables">
                  Disable All
                </UButton>
              </div>
            </div>

            <!-- Variable Search Filter -->
            <div v-if="observableVariables.length > 5">
              <UInput
                v-model="variableSearch"
                placeholder="Filter variables by ID or name..."
                icon="i-lucide-search"
                class="w-full sm:w-80"
                size="sm"
              />
            </div>

            <!-- Variables Table -->
            <div v-if="observableVariables.length > 0" class="border border-neutral-200 rounded-lg overflow-hidden bg-white">
              <UTable
                :data="filteredVariables"
                :columns="observableColumns"
                :meta="observableTableMeta"
                :ui="{
                  th: 'py-2.5 px-3 text-xs font-semibold text-neutral-700 bg-neutral-100 border-b border-neutral-200 whitespace-nowrap',
                  td: 'py-2 px-3 text-xs whitespace-nowrap'
                }"
                sticky
                class="max-h-72 overflow-auto lenis-prevent cursor-pointer"
                data-lenis-prevent
                empty="No variables match your search."
                @select="(_e: Event, row: any) => { row.original.selected = !row.original.selected }"
              >
                <template #enabled-cell="{ row }">
                  <div class="flex justify-center" @click.stop>
                    <USwitch
                      v-model="row.original.selected"
                      size="xs"
                      color="primary"
                      aria-label="Toggle variable status"
                    />
                  </div>
                </template>

                <template #id-cell="{ row }">
                  <span class="font-mono font-medium text-neutral-900">{{ row.original.id }}</span>
                </template>

                <template #name-cell="{ row }">
                  <span class="text-neutral-700">{{ row.original.name || row.original.id }}</span>
                </template>

                <template #target-cell="{ row }">
                  <UTooltip
                    v-if="row.original.symbol || row.original.target"
                    :text="row.original.symbol || row.original.target"
                    :delay-duration="0"
                    :content="{ side: 'top', align: 'start' }"
                  >
                    <div class="max-w-sm truncate cursor-help">
                      <span v-if="row.original.symbol" class="text-primary font-medium">{{ row.original.symbol }}</span>
                      <span v-else>{{ row.original.target }}</span>
                    </div>
                  </UTooltip>
                  <span v-else class="text-neutral-400">—</span>
                </template>
              </UTable>
            </div>

            <div v-else class="p-6 text-center border border-dashed border-neutral-300 rounded-lg text-neutral-500 text-xs">
              No observable variables loaded.
            </div>
          </div>

          <!-- Navigation Footer -->
          <div class="flex items-center justify-between pt-6 border-t border-neutral-100">
            <UButton
              color="neutral"
              variant="outline"
              leading-icon="i-lucide-arrow-left"
              @click="goToStep(2)"
            >
              Back
            </UButton>
            <UButton
              color="primary"
              trailing-icon="i-lucide-arrow-right"
              :disabled="!canAdvanceStep4"
              @click="goToStep(4)"
            >
              Review & Export Archive
            </UButton>
          </div>
        </div>

        <!-- STEP 5: REVIEW & EXPORT COMBINE ARCHIVE -->
        <div v-else-if="activeStep === 4" class="space-y-6">
          <div class="border-b border-neutral-100 pb-4">
            <h2 class="text-lg font-semibold text-neutral-900 flex items-center gap-2">
              <UIcon name="i-lucide-download" class="size-5 text-primary" />
              Compile & Export COMBINE Archive
            </h2>
            <p class="text-xs text-neutral-500 mt-1">
              Verify your experiment configuration and generate the complete .omex archive package.
            </p>
          </div>

          <!-- Summary Grid -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <!-- Box 1: Model & Archive -->
            <div class="p-4 bg-neutral-50 rounded-lg border border-neutral-200 space-y-3">
              <h3 class="text-xs font-semibold text-neutral-600 uppercase tracking-wider flex items-center gap-1.5">
                <UIcon name="i-lucide-file-text" class="size-4 text-primary" />
                Model & Container
              </h3>
              <div class="space-y-1.5 text-xs">
                <div class="flex justify-between">
                  <span class="text-neutral-500">Archive Name:</span>
                  <span class="font-mono font-medium text-neutral-900">{{ archiveName }}.omex</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-neutral-500">Language:</span>
                  <span class="font-medium text-neutral-900">{{ selectedLanguage.name }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-neutral-500">Source:</span>
                  <span class="text-neutral-900 truncate max-w-xs" :title="selectedFile ? selectedFile.name : modelUrl">
                    {{ selectedFile ? selectedFile.name : modelUrl }}
                  </span>
                </div>
              </div>
            </div>

            <!-- Box 2: Method & Conditions -->
            <div class="p-4 bg-neutral-50 rounded-lg border border-neutral-200 space-y-3">
              <h3 class="text-xs font-semibold text-neutral-600 uppercase tracking-wider flex items-center gap-1.5">
                <UIcon name="i-lucide-settings" class="size-4 text-primary" />
                Simulation Specifications
              </h3>
              <div class="space-y-1.5 text-xs">
                <div class="flex justify-between">
                  <span class="text-neutral-500">Framework:</span>
                  <span class="font-medium text-neutral-900">{{ currentFramework.name }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-neutral-500">Simulation Type:</span>
                  <span class="font-medium text-neutral-900">{{ currentSimType.name }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-neutral-500">Algorithm KiSAO:</span>
                  <span class="font-mono text-neutral-900">{{ selectedAlgorithmId }}</span>
                </div>
                <div v-if="selectedSimType === 'SedUniformTimeCourseSimulation'" class="flex justify-between">
                  <span class="text-neutral-500">Time Range:</span>
                  <span class="font-mono text-neutral-900">[{{ timeCourse.outputStartTime }}, {{ timeCourse.outputEndTime }}], {{ timeCourse.numberOfSteps }} steps</span>
                </div>
              </div>
            </div>

            <!-- Box 3: Customization Summary -->
            <div class="p-4 bg-neutral-50 rounded-lg border border-neutral-200 space-y-3 md:col-span-2">
              <h3 class="text-xs font-semibold text-neutral-600 uppercase tracking-wider flex items-center gap-1.5">
                <UIcon name="i-lucide-check-square" class="size-4 text-primary" />
                Customizations & Outputs
              </h3>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <span class="text-neutral-500 block">Parameter Overrides:</span>
                  <span class="font-semibold text-neutral-900">{{ activeParameterChanges.length }} active</span>
                </div>
                <div>
                  <span class="text-neutral-500 block">Recorded Observables:</span>
                  <span class="font-semibold text-neutral-900">{{ selectedVariablesCount }} variable(s)</span>
                </div>
                <div>
                  <span class="text-neutral-500 block">SED-ML Outputs:</span>
                  <span class="font-semibold text-neutral-900">1 Report, 1 2D Plot</span>
                </div>
                <div>
                  <span class="text-neutral-500 block">Recommended Simulator:</span>
                  <span class="font-mono font-medium text-primary">{{ suggestedSimulator }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Build Action or Success Banner -->
          <div v-if="!compilationSuccess" class="p-6 bg-neutral-100 rounded-lg border border-neutral-200 flex flex-col items-center justify-center text-center space-y-4">
            <div>
              <h3 class="text-base font-semibold text-neutral-900">Ready to Compile Experiment</h3>
              <p class="text-xs text-neutral-500 mt-1 max-w-lg">
                Clicking the button below will assemble your SED-ML document, package the model file, and compile the COMBINE archive via the COMBINE API.
              </p>
            </div>

            <UButton
              size="lg"
              color="primary"
              icon="i-lucide-download"
              :loading="isCompiling"
              @click="handleCompileAndDownload"
            >
              Compile & Download COMBINE Archive
            </UButton>
          </div>

          <!-- Success Banner -->
          <div v-else class="p-6 bg-success/5 rounded-lg border border-success/30 space-y-4">
            <div class="flex items-center gap-3">
              <UIcon name="i-lucide-check-circle-2" class="size-8 text-success" />
              <div>
                <h3 class="text-base font-semibold text-neutral-900">COMBINE Archive Downloaded Successfully!</h3>
                <p class="text-xs text-neutral-600 mt-0.5">
                  Your archive <span class="font-mono font-semibold text-neutral-900">{{ archiveName }}.omex</span> ({{ (createdBlobSize / 1024).toFixed(1) }} KB) is ready.
                </p>
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-3 pt-2">
              <UButton
                color="primary"
                icon="i-lucide-play"
                :to="runSimulationUrl"
              >
                Run Simulation on BioSimulations
              </UButton>

              <UButton
                color="neutral"
                variant="outline"
                icon="i-lucide-download"
                @click="downloadAgain"
              >
                Download Again
              </UButton>

              <UButton
                color="neutral"
                variant="ghost"
                icon="i-lucide-plus"
                @click="resetWizard"
              >
                Configure Another Experiment
              </UButton>
            </div>
          </div>

          <!-- Navigation Footer -->
          <div class="flex items-center justify-between pt-6 border-t border-neutral-100">
            <UButton
              color="neutral"
              variant="outline"
              leading-icon="i-lucide-arrow-left"
              @click="goToStep(3)"
            >
              Back
            </UButton>
          </div>
        </div>
      </UCard>
    </div>

    <!-- Add / Edit Custom Parameter Modal -->
    <UModal
      v-model:open="isCustomParamModalOpen"
      :title="editingCustomParamIndex !== null ? 'Edit Custom Parameter' : 'Add Custom Parameter'"
      description="Configure a SED-ML model perturbation by specifying its unique SId, target XPath, and value."
    >
      <template #body>
        <div class="space-y-4 p-4 text-xs">
          <!-- Parameter Name -->
          <div class="space-y-1">
            <label class="block font-semibold text-neutral-800">
              Parameter Name <span class="text-neutral-400 font-normal">(Optional)</span>
            </label>
            <UInput
              v-model="customParamForm.name"
              placeholder="e.g. Initial ATP Concentration"
              class="w-full"
              size="sm"
              @update:model-value="onCustomNameInput"
            />
            <p class="text-[11px] text-neutral-500">
              Descriptive label identifying this perturbation in plots and reports.
            </p>
          </div>

          <!-- Parameter ID (SId) -->
          <div class="space-y-1">
            <label class="block font-semibold text-neutral-800">
              Parameter Identifier (SId) <span class="text-error">*</span>
            </label>
            <UInput
              v-model="customParamForm.id"
              placeholder="e.g. custom_param_1"
              class="w-full font-mono"
              size="sm"
              @update:model-value="isIdTouched = true"
            />
            <p class="text-[11px] text-neutral-500">
              Must be a valid SED-ML SId (alphanumeric and underscores, starting with letter or underscore).
            </p>
          </div>

          <!-- Target XPath -->
          <div class="space-y-1">
            <label class="block font-semibold text-neutral-800">
              Target XPath Expression <span class="text-error">*</span>
            </label>
            <UInput
              v-model="customParamForm.target"
              placeholder="/sbml:sbml/sbml:model/..."
              class="w-full font-mono text-xs"
              size="sm"
            />
            <p class="text-[11px] text-neutral-500">
              Points into the model XML hierarchy to identify the exact attribute to modify.
            </p>

            <!-- Preset Templates (SBML) -->
            <div v-if="selectedLanguage.id === 'SBML'" class="pt-1.5 space-y-1.5">
              <span class="text-[11px] font-semibold text-neutral-600 block">SBML XPath Templates:</span>
              <div class="flex flex-wrap gap-1.5">
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  class="text-[10px]"
                  @click="applyXPathPreset('/sbml:sbml/sbml:model/sbml:listOfParameters/sbml:parameter[@id=\'PARAM_ID\']/@value')"
                >
                  Parameter Value
                </UButton>
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  class="text-[10px]"
                  @click="applyXPathPreset('/sbml:sbml/sbml:model/sbml:listOfSpecies/sbml:species[@id=\'SPECIES_ID\']/@initialConcentration')"
                >
                  Species Initial Concentration
                </UButton>
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  class="text-[10px]"
                  @click="applyXPathPreset('/sbml:sbml/sbml:model/sbml:listOfSpecies/sbml:species[@id=\'SPECIES_ID\']/@initialAmount')"
                >
                  Species Initial Amount
                </UButton>
                <UButton
                  size="xs"
                  color="neutral"
                  variant="outline"
                  class="text-[10px]"
                  @click="applyXPathPreset('/sbml:sbml/sbml:model/sbml:listOfCompartments/sbml:compartment[@id=\'COMP_ID\']/@size')"
                >
                  Compartment Size
                </UButton>
              </div>
            </div>
          </div>

          <!-- New Value -->
          <div class="space-y-1">
            <label class="block font-semibold text-neutral-800">
              Perturbation Value <span class="text-error">*</span>
            </label>
            <UInput
              v-model="customParamForm.newValue"
              placeholder="e.g. 2.5"
              class="w-full font-mono"
              size="sm"
            />
            <p class="text-[11px] text-neutral-500">
              Target numerical value to be assigned during simulation execution.
            </p>
          </div>

          <!-- Error Alert -->
          <UAlert
            v-if="customParamError"
            color="error"
            variant="subtle"
            icon="i-lucide-alert-circle"
            :title="customParamError"
          />
        </div>
      </template>

      <template #footer>
        <div class="flex items-center justify-between w-full p-4 pt-0">
          <div>
            <UButton
              v-if="editingCustomParamIndex !== null"
              label="Delete"
              color="error"
              variant="soft"
              icon="i-lucide-trash-2"
              size="sm"
              @click="deleteFromModal"
            />
          </div>
          <div class="flex items-center gap-2">
            <UButton
              label="Cancel"
              color="neutral"
              variant="ghost"
              size="sm"
              @click="isCustomParamModalOpen = false"
            />
            <UButton
              :label="editingCustomParamIndex !== null ? 'Save Changes' : 'Add Parameter'"
              color="primary"
              size="sm"
              icon="i-lucide-check"
              @click="saveCustomParam"
            />
          </div>
        </div>
      </template>
    </UModal>
  </div>
</template>
