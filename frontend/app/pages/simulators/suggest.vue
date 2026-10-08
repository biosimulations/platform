<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import type { BreadcrumbItem } from '#ui/components/Breadcrumb.vue';
import type { AccordionItem } from '#ui/components/Accordion.vue';
import {
  type RawSimulator,
  type SimulatorCurationStatus,
  getSimulatorCurationStatus,
  getSimulatorCurationStatusMessage,
  KNOWN_EDAM_FORMATS
} from '~/models/simulator-table';
import {
  AlgorithmSubstitutionPolicyLevels,
  type AlgorithmSubstitutionPolicy
} from '~/models/combine-api';
import { useCombineApi } from '~/composables/useCombineApi';

definePageMeta({
  alias: ['/utilities/suggest-simulator']
});

useSeoMeta({
  title: 'Suggest Simulator - BioSimulations',
  ogTitle: 'Suggest Simulator - BioSimulations',
  description: 'Traverse the Kinetic Simulation Algorithm Ontology (KiSAO) graph to find compatible simulation algorithms and discover matching BioSimulators execution engines.',
  ogDescription: 'Traverse the Kinetic Simulation Algorithm Ontology (KiSAO) graph to find compatible simulation algorithms and discover matching BioSimulators execution engines.'
});

const route = useRoute();
const router = useRouter();
const combineApi = useCombineApi();

const isFromUtilities = computed(() => route.path.startsWith('/utilities'));

const breadcrumbs = computed<BreadcrumbItem[]>(() => {
  if (isFromUtilities.value) {
    return [
      { label: 'Home', icon: 'i-lucide-home', to: '/' },
      { label: 'Utilities', to: '/utilities' },
      { label: 'Suggest Simulator' }
    ];
  }
  return [
    { label: 'Home', icon: 'i-lucide-home', to: '/' },
    { label: 'Simulators', to: '/simulators' },
    { label: 'Suggest Simulator' }
  ];
});

// =============================================================================
// Interfaces & Types
// =============================================================================

interface SimulatorItem {
  id: string;
  name: string;
  version: string;
  description: string;
  modelFormats: string[];
  frameworks: string[];
  curationStatus: SimulatorCurationStatus;
  curationStatusMessage: string;
  image?: string;
  url: string;
  implementedAlgorithms: Map<string, string>; // algId -> algName
}

interface AlgorithmOption {
  id: string;
  name: string;
  label: string;
  directCount: number;
  altCount: number;
  totalCount: number;
}

interface MatchedSimulator {
  simulator: SimulatorItem;
  minPolicy?: AlgorithmSubstitutionPolicy;
  implementedMatchingAlgorithms: Array<{
    id: string;
    name: string;
    url: string;
  }>;
}

interface PolicyGroup {
  minPolicy: AlgorithmSubstitutionPolicy;
  simulators: MatchedSimulator[];
}

interface RelatedAlgorithmGroup {
  minPolicy: AlgorithmSubstitutionPolicy;
  algorithms: Array<{
    id: string;
    name: string;
    url: string;
  }>;
}

interface FlatRelatedAlgorithm {
  id: string;
  name: string;
  url: string;
  minPolicy: AlgorithmSubstitutionPolicy;
}

// =============================================================================
// Policy metadata and explanations
// =============================================================================

const POLICY_INFO: Record<string, { label: string; description: string; badgeColor: 'primary' | 'secondary' | 'success' | 'info' | 'warning' | 'error' | 'neutral' }> = {
  SAME_METHOD: {
    label: 'Same Method (Level 1)',
    description: 'Natively implements the exact selected simulation algorithm.',
    badgeColor: 'success'
  },
  SAME_MATH: {
    label: 'Same Math (Level 2)',
    description: 'Solves the identical mathematical equations using an analytically equivalent formulation.',
    badgeColor: 'info'
  },
  SIMILAR_APPROXIMATIONS: {
    label: 'Similar Approximations (Level 3)',
    description: 'Employs numerical approximations of comparable mathematical order and error bounds.',
    badgeColor: 'primary'
  },
  DISTINCT_APPROXIMATIONS: {
    label: 'Distinct Approximations (Level 4)',
    description: 'Uses alternative numerical approximation schemes (e.g. tau-leaping approximations vs direct SSA).',
    badgeColor: 'secondary'
  },
  DISTINCT_SCALES: {
    label: 'Distinct Scales (Level 5)',
    description: 'Executes across distinct spatial or temporal multi-scale formulations.',
    badgeColor: 'warning'
  },
  SAME_VARIABLES: {
    label: 'Same Variables (Level 6)',
    description: 'Operates on the same biological state variables.',
    badgeColor: 'neutral'
  },
  SIMILAR_VARIABLES: {
    label: 'Similar Variables (Level 7)',
    description: 'Operates on mathematically transformed or mapped state variables.',
    badgeColor: 'neutral'
  },
  SAME_FRAMEWORK: {
    label: 'Same Framework (Level 8)',
    description: 'Belongs to the same broad modeling framework (e.g. stochastic discrete or continuous deterministic).',
    badgeColor: 'neutral'
  }
};

function getPolicyInfo(policyId: string) {
  return POLICY_INFO[policyId] || {
    label: `${policyId}`,
    description: 'Alternative compatible algorithm substitution based on the KiSAO ontology.',
    badgeColor: 'neutral' as const
  };
}

function getKisaoUrl(id: string): string {
  const oboId = id.replace('_', ':');
  return `https://www.ebi.ac.uk/ols4/ontologies/kisao/terms?obo_id=${encodeURIComponent(oboId)}`;
}

// =============================================================================
// State
// =============================================================================

const loading = ref(true);
const error = ref<string | null>(null);

const simulatorsMap = ref<Map<string, SimulatorItem>>(new Map());
const algorithmNames = ref<Map<string, string>>(new Map());
const algorithmDirectSims = ref<Map<string, Set<string>>>(new Map()); // algId -> Set<simId>

// Precomputed mapping for suggestions: algId -> { directSims: Set<simId>, altSims: Map<simId, { minPolicy, algs: Set<string> }>, altAlgs: Map<level, { policy, algs: Set<string> }> }
interface PrecomputedAlgData {
  directSimIds: Set<string>;
  altSims: Map<string, { minPolicy: AlgorithmSubstitutionPolicy; algIds: Set<string> }>;
  altAlgs: Map<number, { minPolicy: AlgorithmSubstitutionPolicy; algIds: Set<string> }>;
}

const suggestionLookup = ref<Map<string, PrecomputedAlgData>>(new Map());

const selectedAlgorithmId = ref<string | null>(null);

// Accordion active sections (controlled for default state based on results count)
const activeAccordionSections = ref<string[]>(['direct-matches', 'alt-simulators', 'related-algorithms']);

// Quick select popular algorithms
const popularAlgorithms = [
  { id: 'KISAO_0000019', name: 'CVODE (ODE solver)' },
  { id: 'KISAO_0000029', name: 'Gillespie direct (SSA)' },
  { id: 'KISAO_0000437', name: 'Flux balance analysis (FBA)' },
  { id: 'KISAO_0000088', name: 'Euler forward method' },
  { id: 'KISAO_0000032', name: 'RK4 (Runge-Kutta 4th)' },
  { id: 'KISAO_0000560', name: 'LSODA (Stiff / Non-stiff)' }
];

// =============================================================================
// Data Loading
// =============================================================================

async function loadData() {
  loading.value = true;
  error.value = null;

  try {
    // 1. Fetch latest simulators from api.biosimulators.org
    const rawSimulators = await $fetch<RawSimulator[]>('https://api.biosimulators.org/simulators/latest', {
      credentials: 'omit'
    });

    const simMap = new Map<string, SimulatorItem>();
    const algNames = new Map<string, string>();
    const algSims = new Map<string, Set<string>>();
    const uniqueKisaoIds = new Set<string>();

    for (const raw of rawSimulators || []) {
      const status = getSimulatorCurationStatus(raw);
      const statusMsg = getSimulatorCurationStatusMessage(status);

      const algImplMap = new Map<string, string>();
      for (const alg of raw.algorithms || []) {
        const id = alg.kisaoId?.id || alg.id;
        if (id && id.startsWith('KISAO_')) {
          uniqueKisaoIds.add(id);
          const algName = alg.name || id;
          algImplMap.set(id, algName);
          if (alg.name && !algNames.has(id)) {
            algNames.set(id, alg.name);
          }

          if (!algSims.has(id)) {
            algSims.set(id, new Set());
          }
          algSims.get(id)!.add(raw.id);
        }
      }

      // Format names
      const modelFormats = (raw.algorithms || [])
        .flatMap(a => (a.modelFormats || []).map(f => KNOWN_EDAM_FORMATS[f.id] || f.id))
        .filter((v, i, a) => a.indexOf(v) === i);

      const frameworks = (raw.algorithms || [])
        .flatMap(a => (a.modelingFrameworks || []).map(f => f.id))
        .filter((v, i, a) => a.indexOf(v) === i);

      simMap.set(raw.id, {
        id: raw.id,
        name: raw.name,
        version: raw.version,
        description: raw.description || 'No description provided.',
        modelFormats,
        frameworks,
        curationStatus: status,
        curationStatusMessage: statusMsg,
        image: raw.image?.url,
        url: `/simulators/${raw.id}`,
        implementedAlgorithms: algImplMap
      });
    }

    simulatorsMap.value = simMap;

    // 2. Fetch algorithm substitutions from COMBINE API
    const substitutions = await combineApi.getSimilarAlgorithms(Array.from(uniqueKisaoIds));

    // Register any names returned from COMBINE API
    for (const sub of substitutions) {
      for (const alg of sub.algorithms) {
        if (alg.name && (!algNames.has(alg.id) || algNames.get(alg.id) === alg.id)) {
          algNames.set(alg.id, alg.name);
        }
      }
    }

    algorithmNames.value = algNames;
    algorithmDirectSims.value = algSims;

    // 3. Build fast lookup index for all algorithms
    const lookup = new Map<string, PrecomputedAlgData>();

    for (const algId of uniqueKisaoIds) {
      lookup.set(algId, {
        directSimIds: new Set(algSims.get(algId) || []),
        altSims: new Map(),
        altAlgs: new Map()
      });
    }

    // Process substitutions
    for (const sub of substitutions) {
      if (!sub.algorithms || sub.algorithms.length < 2) continue;
      const mainAlg = sub.algorithms[0];
      const altAlg = sub.algorithms[1];
      if (!mainAlg || !altAlg || !mainAlg.id || !altAlg.id) continue;
      const mainId = mainAlg.id;
      const altId = altAlg.id;
      const policy = sub.minPolicy;

      // Ensure both algorithms have lookup entries
      if (!lookup.has(mainId)) {
        lookup.set(mainId, {
          directSimIds: new Set(algSims.get(mainId) || []),
          altSims: new Map(),
          altAlgs: new Map()
        });
      }
      if (!lookup.has(altId)) {
        lookup.set(altId, {
          directSimIds: new Set(algSims.get(altId) || []),
          altSims: new Map(),
          altAlgs: new Map()
        });
      }

      const mainData = lookup.get(mainId)!;
      const altData = lookup.get(altId)!;

      // Alt algorithms index (only policy levels > 1)
      if (policy.level > AlgorithmSubstitutionPolicyLevels.SAME_METHOD) {
        if (!mainData.altAlgs.has(policy.level)) {
          mainData.altAlgs.set(policy.level, { minPolicy: policy, algIds: new Set() });
        }
        mainData.altAlgs.get(policy.level)!.algIds.add(altId);

        if (!altData.altAlgs.has(policy.level)) {
          altData.altAlgs.set(policy.level, { minPolicy: policy, algIds: new Set() });
        }
        altData.altAlgs.get(policy.level)!.algIds.add(mainId);
      }

      // Simulator substitutions:
      // Simulators that implement mainAlg -> alternative match for altAlg
      const mainSims = algSims.get(mainId) || new Set<string>();
      for (const simId of mainSims) {
        // If this simulator already directly implements altAlg, it stays as direct (Level 1)
        if (altData.directSimIds.has(simId)) continue;

        const existing = altData.altSims.get(simId);
        if (!existing) {
          altData.altSims.set(simId, { minPolicy: policy, algIds: new Set([mainId]) });
        } else if (policy.level < existing.minPolicy.level) {
          altData.altSims.set(simId, { minPolicy: policy, algIds: new Set([mainId]) });
        } else if (policy.level === existing.minPolicy.level) {
          existing.algIds.add(mainId);
        }
      }

      // Simulators that implement altAlg -> alternative match for mainAlg
      const altSims = algSims.get(altId) || new Set<string>();
      for (const simId of altSims) {
        if (mainData.directSimIds.has(simId)) continue;

        const existing = mainData.altSims.get(simId);
        if (!existing) {
          mainData.altSims.set(simId, { minPolicy: policy, algIds: new Set([altId]) });
        } else if (policy.level < existing.minPolicy.level) {
          mainData.altSims.set(simId, { minPolicy: policy, algIds: new Set([altId]) });
        } else if (policy.level === existing.minPolicy.level) {
          existing.algIds.add(altId);
        }
      }
    }

    suggestionLookup.value = lookup;

    // 4. Check query params for initial selection
    const queryAlg = (route.query.algorithm || route.query.simulationAlgorithm || route.query.kisaoId) as string | undefined;
    if (queryAlg && lookup.has(queryAlg)) {
      selectedAlgorithmId.value = queryAlg;
    }
  } catch (err: any) {
    console.error('Failed to load simulator suggestion data:', err);
    error.value = err?.message || 'Failed to load simulator and KiSAO ontology data. Please try again.';
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadData();
});

// Sync selection to query param
watch(selectedAlgorithmId, (newId) => {
  if (newId) {
    router.replace({
      query: {
        ...route.query,
        algorithm: newId
      }
    });
  } else {
    const q = { ...route.query };
    delete q.algorithm;
    delete q.simulationAlgorithm;
    delete q.kisaoId;
    router.replace({ query: q });
  }
});

// Watch query param changes (e.g. back button)
watch(() => route.query.algorithm, (newVal) => {
  if (typeof newVal === 'string' && newVal !== selectedAlgorithmId.value) {
    if (suggestionLookup.value.has(newVal)) {
      selectedAlgorithmId.value = newVal;
    }
  }
});

// =============================================================================
// Computed Options & Active Suggestions
// =============================================================================

const algorithmOptions = computed<AlgorithmOption[]>(() => {
  const options: AlgorithmOption[] = [];
  const lookup = suggestionLookup.value;
  const names = algorithmNames.value;

  for (const [id, data] of lookup.entries()) {
    const name = names.get(id) || id;
    const directCount = data.directSimIds.size;
    const altCount = data.altSims.size;
    const totalCount = directCount + altCount;

    options.push({
      id,
      name,
      label: `${name} (${id})`,
      directCount,
      altCount,
      totalCount
    });
  }

  // Sort alphabetically by algorithm name
  return options.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
});

const selectedAlgorithmData = computed(() => {
  if (!selectedAlgorithmId.value) return null;
  const data = suggestionLookup.value.get(selectedAlgorithmId.value);
  if (!data) return null;

  const algName = algorithmNames.value.get(selectedAlgorithmId.value) || selectedAlgorithmId.value;

  return {
    id: selectedAlgorithmId.value,
    name: algName,
    url: getKisaoUrl(selectedAlgorithmId.value),
    data
  };
});

// Direct simulator matches (Level 1: Same Method)
const directSimulatorMatches = computed<SimulatorItem[]>(() => {
  if (!selectedAlgorithmData.value) return [];
  const directIds = Array.from(selectedAlgorithmData.value.data.directSimIds);
  const sims = directIds
    .map(id => simulatorsMap.value.get(id))
    .filter((s): s is SimulatorItem => !!s);

  return sims.sort((a, b) => a.name.localeCompare(b.name));
});

// Alternative compatible simulators grouped by policy level
const alternativePolicyGroups = computed<PolicyGroup[]>(() => {
  if (!selectedAlgorithmData.value) return [];
  const altSims = selectedAlgorithmData.value.data.altSims;
  const groupsByLevel = new Map<number, { minPolicy: AlgorithmSubstitutionPolicy; simulators: MatchedSimulator[] }>();

  for (const [simId, match] of altSims.entries()) {
    const sim = simulatorsMap.value.get(simId);
    if (!sim) continue;

    const level = match.minPolicy.level;
    if (!groupsByLevel.has(level)) {
      groupsByLevel.set(level, {
        minPolicy: match.minPolicy,
        simulators: []
      });
    }

    const implementedAlgs = Array.from(match.algIds).map(algId => ({
      id: algId,
      name: algorithmNames.value.get(algId) || algId,
      url: getKisaoUrl(algId)
    })).sort((a, b) => a.name.localeCompare(b.name));

    groupsByLevel.get(level)!.simulators.push({
      simulator: sim,
      minPolicy: match.minPolicy,
      implementedMatchingAlgorithms: implementedAlgs
    });
  }

  // Sort simulators within each level alphabetically
  for (const group of groupsByLevel.values()) {
    group.simulators.sort((a, b) => a.simulator.name.localeCompare(b.simulator.name));
  }

  // Sort groups by policy level ascending (closest similarity first)
  return Array.from(groupsByLevel.values()).sort((a, b) => a.minPolicy.level - b.minPolicy.level);
});

// Total count of alternative simulators
const totalAlternativeSimulatorsCount = computed(() => {
  return alternativePolicyGroups.value.reduce((acc, g) => acc + g.simulators.length, 0);
});

// Related algorithms in KiSAO hierarchy
const relatedAlgorithmGroups = computed<RelatedAlgorithmGroup[]>(() => {
  if (!selectedAlgorithmData.value) return [];
  const altAlgs = selectedAlgorithmData.value.data.altAlgs;
  const groups: RelatedAlgorithmGroup[] = [];

  for (const [_, data] of altAlgs.entries()) {
    const algs = Array.from(data.algIds).map(id => ({
      id,
      name: algorithmNames.value.get(id) || id,
      url: getKisaoUrl(id)
    })).sort((a, b) => a.name.localeCompare(b.name));

    groups.push({
      minPolicy: data.minPolicy,
      algorithms: algs
    });
  }

  return groups.sort((a, b) => a.minPolicy.level - b.minPolicy.level);
});

// Flattened related algorithms list for grid display
const allRelatedAlgorithms = computed<FlatRelatedAlgorithm[]>(() => {
  const result: FlatRelatedAlgorithm[] = [];
  for (const group of relatedAlgorithmGroups.value) {
    for (const alg of group.algorithms) {
      result.push({
        ...alg,
        minPolicy: group.minPolicy
      });
    }
  }
  return result;
});

const totalRelatedAlgorithmsCount = computed(() => {
  return allRelatedAlgorithms.value.length;
});

const totalCompatibleSimulatorsCount = computed(() => {
  if (!selectedAlgorithmData.value) return 0;
  return selectedAlgorithmData.value.data.directSimIds.size + selectedAlgorithmData.value.data.altSims.size;
});

interface SuggestionAccordionItem extends AccordionItem {
  value: string;
  slot: string;
  title: string;
  label: string;
  count: number;
  badgeLabel?: string;
  badgeColor?: 'neutral' | 'primary' | 'secondary' | 'success' | 'info' | 'warning' | 'error';
  description: string;
}

const accordionItems = computed<SuggestionAccordionItem[]>(() => {
  const directCount = directSimulatorMatches.value.length;
  const altCount = totalAlternativeSimulatorsCount.value;
  const relatedCount = totalRelatedAlgorithmsCount.value;

  return [
    {
      value: 'direct-matches',
      slot: 'direct-matches',
      title: 'Direct Simulator Matches',
      label: directCount === 0 ? 'Direct Simulator Matches - 0 results' : 'Direct Simulator Matches',
      count: directCount,
      badgeLabel: directCount > 0 ? `Level 1: Same Method (${directCount})` : undefined,
      badgeColor: 'success',
      description: 'Simulation engines that natively implement the exact selected algorithm.'
    },
    {
      value: 'alt-simulators',
      slot: 'alt-simulators',
      title: 'Alternative Compatible Simulators',
      label: altCount === 0 ? 'Alternative Compatible Simulators - 0 results' : 'Alternative Compatible Simulators',
      count: altCount,
      badgeLabel: altCount > 0 ? `${altCount} Compatible ${altCount === 1 ? 'Simulator' : 'Simulators'}` : undefined,
      badgeColor: 'info',
      description: 'Engines supporting substituted algorithms grouped in descending order of ontology similarity.'
    },
    {
      value: 'related-algorithms',
      slot: 'related-algorithms',
      title: 'Related KiSAO Algorithms',
      label: relatedCount === 0 ? 'Related KiSAO Algorithms - 0 results' : 'Related KiSAO Algorithms',
      count: relatedCount,
      badgeLabel: relatedCount > 0 ? `${relatedCount} Related ${relatedCount === 1 ? 'Term' : 'Terms'}` : undefined,
      badgeColor: 'primary',
      description: 'Algorithms connected to this term in the Kinetic Simulation Algorithm Ontology. Click any card to re-focus the suggestion tool and scroll to results.'
    }
  ];
});

function updateDefaultAccordionSections() {
  const sections: string[] = [];
  if (directSimulatorMatches.value.length > 0) {
    sections.push('direct-matches');
  }
  if (totalAlternativeSimulatorsCount.value > 0) {
    sections.push('alt-simulators');
  }
  if (totalRelatedAlgorithmsCount.value > 0) {
    sections.push('related-algorithms');
  }
  activeAccordionSections.value = sections;
}

// Watch algorithm changes to set default accordion states based on results count
watch(selectedAlgorithmId, () => {
  nextTick(() => {
    updateDefaultAccordionSections();
  });
}, { immediate: true });

function selectAlgorithm(id: string) {
  selectedAlgorithmId.value = id;
}

function selectAlgorithmAndScroll(id: string) {
  selectedAlgorithmId.value = id;
  if (typeof window !== 'undefined') {
    window.scrollTo({
      top: 0,
      behavior: 'smooth'
    });
  }
}

function clearSelection() {
  selectedAlgorithmId.value = null;
}
</script>

<template>
  <div class="min-h-screen bg-neutral-50 py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
      <!-- Breadcrumb & Header -->
      <div class="space-y-4">
        <UBreadcrumb :items="breadcrumbs" class="mb-2">
          <template #separator>
            <span class="mx-1 text-neutral-400">/</span>
          </template>
        </UBreadcrumb>

        <div class="flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <div class="flex items-center gap-3">
              <div class="size-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                <UIcon name="i-lucide-lightbulb" class="size-6" />
              </div>
              <h1 class="text-3xl font-bold tracking-tight text-neutral-900 flex items-center gap-3">
                Suggest Simulator
              </h1>
            </div>
            <p class="mt-2 text-sm sm:text-base text-neutral-600 max-w-3xl leading-relaxed">
              Traverse the Kinetic Simulation Algorithm Ontology (KiSAO) graph to find compatible simulation algorithms and discover matching BioSimulators execution engines.
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-2 shrink-0">
            <UButton
              to="/simulators"
              color="neutral"
              variant="outline"
              icon="i-lucide-layers"
              size="sm"
            >
              Browse All Simulators
            </UButton>
            <UButton
              to="https://www.ebi.ac.uk/ols4/ontologies/kisao"
              target="_blank"
              rel="noopener noreferrer"
              color="neutral"
              variant="outline"
              icon="i-lucide-external-link"
              size="sm"
            >
              KiSAO Ontology
            </UButton>
            <UButton
              to="/utilities/validate-simulation"
              color="neutral"
              variant="outline"
              icon="i-lucide-activity"
              size="sm"
            >
              SED-ML Validator
            </UButton>
          </div>
        </div>
      </div>

      <!-- Loading State -->
      <div v-if="loading" class="space-y-6">
        <UCard class="p-6">
          <div class="space-y-4">
            <USkeleton class="h-6 w-1/3" />
            <USkeleton class="h-10 w-full" />
            <div class="flex gap-2 pt-2">
              <USkeleton class="h-8 w-24" />
              <USkeleton class="h-8 w-32" />
              <USkeleton class="h-8 w-28" />
            </div>
          </div>
        </UCard>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <USkeleton class="h-44 w-full" />
          <USkeleton class="h-44 w-full" />
          <USkeleton class="h-44 w-full" />
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="space-y-4">
        <UAlert
          color="error"
          variant="subtle"
          icon="i-lucide-alert-triangle"
          title="Error Loading Simulator Suggestion Data"
          :description="error"
        >
          <template #actions>
            <UButton
              color="error"
              variant="solid"
              size="sm"
              icon="i-lucide-refresh-cw"
              label="Retry"
              @click="loadData"
            />
          </template>
        </UAlert>
      </div>

      <!-- Main Content -->
      <div v-else class="space-y-8">
        <!-- Algorithm Selector Card -->
        <UCard class="shadow-sm border border-neutral-200">
          <template #header>
            <div class="flex items-center justify-between gap-3">
              <div>
                <h2 class="text-base sm:text-lg font-semibold text-neutral-900 flex items-center gap-2">
                  <UIcon name="i-lucide-search" class="size-5 text-primary" />
                  Select Simulation Algorithm
                </h2>
                <p class="text-xs text-neutral-500 mt-0.5">
                  Choose an algorithm from the KiSAO ontology or search by name or KiSAO identifier.
                </p>
              </div>
              <UBadge color="neutral" variant="subtle" size="sm" class="shrink-0">
                {{ algorithmOptions.length }} Algorithms Available
              </UBadge>
            </div>
          </template>

          <div class="space-y-4">
            <!-- Searchable Select Menu -->
            <div>
              <label for="algorithm-select" class="block text-xs font-medium text-neutral-700 mb-1.5">
                Simulation Algorithm (KiSAO Term)
              </label>
              <USelectMenu
                id="algorithm-select"
                v-model="selectedAlgorithmId"
                value-key="id"
                label-key="label"
                :items="algorithmOptions"
                :filter-fields="['name', 'id', 'label']"
                :search-input="{ placeholder: 'Search by algorithm name or KiSAO ID (e.g. CVODE, Gillespie, SSA, FBA, KISAO_0000019)...' }"
                placeholder="Search or select an algorithm..."
                size="lg"
                clear
                icon="i-lucide-search"
                class="w-full"
              >
                <template #item-label="{ item }">
                  <div class="flex items-center justify-between w-full gap-2">
                    <div class="flex items-center gap-2 min-w-0">
                      <span class="font-medium text-neutral-900 truncate">{{ item.name }}</span>
                      <span class="font-mono text-xs text-neutral-400 shrink-0">({{ item.id }})</span>
                    </div>
                    <div class="flex items-center gap-1 shrink-0">
                      <UBadge
                        v-if="item.directCount > 0"
                        color="success"
                        variant="subtle"
                        size="sm"
                      >
                        {{ item.directCount }} direct
                      </UBadge>
                      <UBadge
                        v-else-if="item.altCount > 0"
                        color="info"
                        variant="subtle"
                        size="sm"
                      >
                        {{ item.altCount }} alt
                      </UBadge>
                    </div>
                  </div>
                </template>
              </USelectMenu>
            </div>

            <!-- Popular Quick Select Chips -->
            <div class="pt-1 border-t border-neutral-100 flex flex-wrap items-center gap-2">
              <span class="text-xs font-semibold text-neutral-500 uppercase tracking-wider">
                Popular Algorithms:
              </span>
              <UButton
                v-for="pop in popularAlgorithms"
                :key="pop.id"
                size="xs"
                :variant="selectedAlgorithmId === pop.id ? 'solid' : 'outline'"
                :color="selectedAlgorithmId === pop.id ? 'primary' : 'neutral'"
                @click="selectAlgorithm(pop.id)"
              >
                {{ pop.name }}
              </UButton>
            </div>
          </div>
        </UCard>

        <!-- Active Selection View: Combined Selection Overview & Results Accordion Card -->
        <UCard
          v-if="selectedAlgorithmData"
          class="border border-neutral-200 shadow-sm overflow-hidden"
          :ui="{
            root: 'rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden divide-y divide-neutral-200 ring-0',
            header: 'p-5 sm:p-6 bg-white',
            body: 'p-0 sm:p-0'
          }"
        >
          <!-- Selection Overview Header -->
          <template #header>
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
              <div class="space-y-2">
                <div class="flex flex-wrap items-center gap-2">
                  <h2 class="text-xl sm:text-2xl font-bold text-neutral-900">
                    {{ selectedAlgorithmData.name }}
                  </h2>
                  <a
                    :href="selectedAlgorithmData.url"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-neutral-100 hover:bg-neutral-200 text-neutral-700 font-mono text-xs font-medium transition-colors"
                    :aria-label="'View ' + selectedAlgorithmData.name + ' in KiSAO ontology (opens in new tab)'"
                  >
                    <span>{{ selectedAlgorithmData.id }}</span>
                    <UIcon name="i-lucide-external-link" class="size-3 text-neutral-400" />
                  </a>
                </div>
                <p class="text-xs sm:text-sm text-neutral-600 leading-relaxed max-w-3xl">
                  Simulators below are categorized by KiSAO compatibility policies. Direct matches execute this exact method; alternatives execute analytically or numerically related algorithms.
                </p>
              </div>

              <!-- Quick Stats -->
              <div class="flex flex-wrap items-center gap-3 shrink-0">
                <div class="px-4 py-2 rounded-lg bg-neutral-50 border border-neutral-200 text-center">
                  <div class="text-lg font-bold text-neutral-900">
                    {{ totalCompatibleSimulatorsCount }}
                  </div>
                  <div class="text-[11px] font-medium text-neutral-500">
                    Compatible Engines
                  </div>
                </div>

                <div class="px-4 py-2 rounded-lg bg-emerald-50 border border-emerald-200 text-center">
                  <div class="text-lg font-bold text-emerald-700">
                    {{ directSimulatorMatches.length }}
                  </div>
                  <div class="text-[11px] font-medium text-emerald-600">
                    Direct Solvers
                  </div>
                </div>

                <div class="px-4 py-2 rounded-lg bg-blue-50 border border-blue-200 text-center">
                  <div class="text-lg font-bold text-blue-700">
                    {{ totalAlternativeSimulatorsCount }}
                  </div>
                  <div class="text-[11px] font-medium text-blue-600">
                    Alternative Solvers
                  </div>
                </div>

                <UButton
                  color="neutral"
                  variant="ghost"
                  icon="i-lucide-x"
                  size="sm"
                  aria-label="Clear algorithm selection"
                  @click="clearSelection"
                >
                  Clear
                </UButton>
              </div>
            </div>
          </template>

          <!-- Comprehensive Accordion for Direct Matches, Alternative Simulators, and Related Algorithms -->
          <UAccordion
            v-model="activeAccordionSections"
            type="multiple"
            :items="accordionItems"
            :ui="{
              root: 'w-full bg-white',
              item: 'border-b border-neutral-200 last:border-b-0',
              header: 'w-full',
              trigger: 'w-full flex items-center justify-between p-4 sm:p-5 hover:bg-neutral-50/80 data-[state=open]:bg-neutral-50/40 transition-colors text-left group cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
              content: 'border-t border-neutral-100 bg-neutral-50/25 p-4 sm:p-6',
              body: 'p-0'
            }"
          >
            <!-- Custom Trigger Label Header -->
            <template #default="{ item }">
              <span class="block space-y-1 py-0.5">
                <span class="flex flex-wrap items-center gap-2">
                  <span class="text-base sm:text-lg font-bold text-neutral-900 leading-tight">
                    {{ item.title }}
                  </span>
                  <span v-if="item.count === 0" class="text-xs text-neutral-400 font-normal">
                    - 0 results
                  </span>
                  <UBadge
                    v-else-if="item.badgeLabel"
                    :color="item.badgeColor"
                    variant="subtle"
                    size="sm"
                  >
                    {{ item.badgeLabel }}
                  </UBadge>
                </span>
                <span class="block text-xs text-neutral-500 font-normal">
                  {{ item.description }}
                </span>
              </span>
            </template>

            <!-- Section 1 Content: Direct Simulator Matches -->
            <template #direct-matches>
              <div id="direct-simulators-section">
                <!-- Direct Simulators Grid -->
                <div v-if="directSimulatorMatches.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                  <UCard
                    v-for="sim in directSimulatorMatches"
                    :key="sim.id"
                    as="article"
                    class="flex flex-col h-full border border-neutral-200 hover:border-emerald-300 transition-colors shadow-sm"
                    :ui="{
                      root: 'flex flex-col h-full',
                      header: 'p-4 sm:p-5 pb-3',
                      body: 'p-4 sm:p-5 pt-0 flex-1 flex flex-col justify-start',
                      footer: 'p-4 sm:p-5 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between gap-2'
                    }"
                  >
                    <template #header>
                      <div class="space-y-1.5 w-full">
                        <!-- Row 1: Name and Version inline and justified between -->
                        <div class="flex items-start justify-between gap-2 w-full">
                          <h4 :id="'direct-sim-' + sim.id" class="font-bold text-base text-neutral-900 leading-snug">
                            {{ sim.name }}
                          </h4>
                          <UBadge color="neutral" variant="subtle" size="sm" class="shrink-0">
                            v{{ sim.version }}
                          </UBadge>
                        </div>

                        <!-- Row 2: ID and Curation Status inline and justified between -->
                        <div class="flex items-center justify-between gap-2 w-full">
                          <span class="text-xs text-neutral-400 font-mono">
                            ID: {{ sim.id }}
                          </span>
                          <UBadge
                            :color="sim.curationStatus >= 4 ? 'success' : 'neutral'"
                            variant="subtle"
                            size="sm"
                            class="shrink-0"
                            :aria-label="'Curation status: ' + sim.curationStatusMessage"
                          >
                            {{ sim.curationStatusMessage }}
                          </UBadge>
                        </div>
                      </div>
                    </template>

                    <div class="space-y-3 text-xs flex-1">
                      <p class="text-neutral-600 leading-relaxed">
                        {{ sim.description }}
                      </p>

                      <div v-if="sim.modelFormats.length > 0" class="space-y-1 pt-1">
                        <span class="text-xs uppercase font-semibold text-neutral-400">Supported Formats:</span>
                        <div class="flex flex-wrap gap-1">
                          <UBadge
                            v-for="fmt in sim.modelFormats.slice(0, 5)"
                            :key="fmt"
                            color="neutral"
                            variant="subtle"
                            size="sm"
                          >
                            {{ fmt }}
                          </UBadge>
                          <UBadge
                            v-if="sim.modelFormats.length > 5"
                            color="neutral"
                            variant="outline"
                            size="sm"
                          >
                            +{{ sim.modelFormats.length - 5 }}
                          </UBadge>
                        </div>
                      </div>
                    </div>

                    <template #footer>
                      <div class="flex items-center justify-between gap-2 w-full">
                        <UButton
                          :to="sim.url"
                          color="neutral"
                          variant="outline"
                          size="sm"
                          icon="i-lucide-info"
                          label="View Details"
                          :aria-label="'View details for ' + sim.name"
                        />
                        <UButton
                          :to="`/simulations/run?simulator=${sim.id}`"
                          color="primary"
                          variant="solid"
                          size="sm"
                          icon="i-fluent-sparkle-20-filled"
                          label="Run Simulation"
                          :aria-label="'Run simulation with ' + sim.name"
                        />
                      </div>
                    </template>
                  </UCard>
                </div>

                <!-- No Direct Matches Notice -->
                <UAlert
                  v-else
                  color="info"
                  variant="subtle"
                  icon="i-lucide-info"
                  title="No Direct Simulator Matches"
                  description="No registered simulation tool on BioSimulations natively implements this exact KiSAO term. However, you can use the compatible alternative engines below."
                />
              </div>
            </template>

            <!-- Section 2 Content: Alternative Compatible Simulators -->
            <template #alt-simulators>
              <div id="alt-simulators-section" class="space-y-6">
                <div v-if="alternativePolicyGroups.length > 0" class="space-y-6">
                  <div
                    v-for="group in alternativePolicyGroups"
                    :key="group.minPolicy.level"
                    class="space-y-4"
                  >
                    <!-- Clean Policy Level Sub-header (seamless divider, no heavy nested card) -->
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-neutral-200/80 pb-2.5">
                      <div class="flex flex-wrap items-center gap-2">
                        <h4 class="font-bold text-base text-neutral-900">
                          {{ group.minPolicy.name }}
                        </h4>
                        <UBadge
                          :color="getPolicyInfo(group.minPolicy.id).badgeColor"
                          variant="subtle"
                          size="sm"
                        >
                          Level {{ group.minPolicy.level }}
                        </UBadge>
                        <span class="text-xs text-neutral-400 hidden sm:inline">&bull;</span>
                        <p class="text-xs text-neutral-500">
                          {{ getPolicyInfo(group.minPolicy.id).description }}
                        </p>
                      </div>
                      <UBadge color="neutral" variant="outline" size="sm" class="shrink-0 self-start sm:self-auto">
                        {{ group.simulators.length }} Compatible {{ group.simulators.length === 1 ? 'Simulator' : 'Simulators' }}
                      </UBadge>
                    </div>

                    <!-- Simulators Grid in this Level -->
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                      <UCard
                        v-for="item in group.simulators"
                        :key="item.simulator.id"
                        as="article"
                        class="flex flex-col h-full border border-neutral-200 hover:border-blue-300 transition-colors shadow-sm"
                        :ui="{
                          root: 'flex flex-col h-full',
                          header: 'p-4 sm:p-5 pb-3',
                          body: 'p-4 sm:p-5 pt-0 flex-1 flex flex-col justify-start',
                          footer: 'p-4 sm:p-5 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between gap-2'
                        }"
                      >
                        <template #header>
                          <div class="space-y-1.5 w-full">
                            <!-- Row 1: Name and Version inline and justified between -->
                            <div class="flex items-start justify-between gap-2 w-full">
                              <h5 :id="'alt-sim-' + item.simulator.id" class="font-bold text-base text-neutral-900 leading-snug">
                                {{ item.simulator.name }}
                              </h5>
                              <UBadge color="neutral" variant="subtle" size="sm" class="shrink-0">
                                v{{ item.simulator.version }}
                              </UBadge>
                            </div>

                            <!-- Row 2: ID and Curation Status inline and justified between -->
                            <div class="flex items-center justify-between gap-2 w-full">
                              <span class="text-xs text-neutral-400 font-mono">
                                ID: {{ item.simulator.id }}
                              </span>
                              <UBadge
                                :color="item.simulator.curationStatus >= 4 ? 'success' : 'neutral'"
                                variant="subtle"
                                size="sm"
                                class="shrink-0"
                                :aria-label="'Curation status: ' + item.simulator.curationStatusMessage"
                              >
                                {{ item.simulator.curationStatusMessage }}
                              </UBadge>
                            </div>
                          </div>
                        </template>

                        <div class="space-y-3 text-xs flex-1">
                          <!-- Full description without truncation -->
                          <p class="text-neutral-600 leading-relaxed">
                            {{ item.simulator.description }}
                          </p>

                          <!-- Supported Formats -->
                          <div v-if="item.simulator.modelFormats.length > 0" class="space-y-1 pt-1">
                            <span class="text-xs uppercase font-semibold text-neutral-400">Supported Formats:</span>
                            <div class="flex flex-wrap gap-1">
                              <UBadge
                                v-for="fmt in item.simulator.modelFormats.slice(0, 5)"
                                :key="fmt"
                                color="neutral"
                                variant="subtle"
                                size="sm"
                              >
                                {{ fmt }}
                              </UBadge>
                              <UBadge
                                v-if="item.simulator.modelFormats.length > 5"
                                color="neutral"
                                variant="outline"
                                size="sm"
                              >
                                +{{ item.simulator.modelFormats.length - 5 }}
                              </UBadge>
                            </div>
                          </div>

                          <!-- Substituted / Compatible Algorithms Executed by this Simulator -->
                          <div v-if="item.implementedMatchingAlgorithms.length > 0" class="p-3 rounded-lg bg-neutral-50 border border-neutral-200/60 space-y-2">
                            <div class="text-xs uppercase font-semibold text-neutral-500 flex items-center gap-1.5">
                              <UIcon name="i-lucide-arrow-right-left" class="size-3.5 text-primary" />
                              Substituted Algorithm(s):
                            </div>
                            <div class="space-y-1.5">
                              <div
                                v-for="subAlg in item.implementedMatchingAlgorithms"
                                :key="subAlg.id"
                                class="flex items-center justify-between gap-2 text-xs"
                              >
                                <span class="font-medium text-neutral-800 truncate">{{ subAlg.name }}</span>
                                <a
                                  :href="subAlg.url"
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  class="font-mono text-xs text-primary hover:underline shrink-0"
                                  :aria-label="'View ' + subAlg.name + ' (' + subAlg.id + ') on EBI OLS4 (opens in new tab)'"
                                >
                                  {{ subAlg.id }}
                                </a>
                              </div>
                            </div>
                          </div>
                        </div>

                        <template #footer>
                          <div class="flex items-center justify-between gap-2 w-full">
                            <UButton
                              :to="item.simulator.url"
                              color="neutral"
                              variant="outline"
                              size="sm"
                              icon="i-lucide-info"
                              label="View Details"
                              :aria-label="'View details for ' + item.simulator.name"
                            />
                            <UButton
                              :to="`/simulations/run?simulator=${item.simulator.id}`"
                              color="primary"
                              variant="solid"
                              size="sm"
                              icon="i-fluent-sparkle-20-filled"
                              label="Run Simulation"
                              :aria-label="'Run simulation with ' + item.simulator.name"
                            />
                          </div>
                        </template>
                      </UCard>
                    </div>
                  </div>
                </div>

                <UAlert
                  v-else
                  color="info"
                  variant="subtle"
                  icon="i-lucide-info"
                  title="No Alternative Compatible Simulators"
                  description="No substitute simulation tools were found for this algorithm within the supported KiSAO substitution policies."
                />
              </div>
            </template>

            <!-- Section 3 Content: Related KiSAO Algorithms (Grid of Cards) -->
            <template #related-algorithms>
              <div id="related-algorithms-section">
                <!-- Grid of Cards for Related Algorithms: Requirement 3 -->
                <div v-if="allRelatedAlgorithms.length > 0" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                  <UCard
                    v-for="relAlg in allRelatedAlgorithms"
                    :key="relAlg.id"
                    as="article"
                    class="flex flex-col h-full border border-neutral-200 hover:border-primary/50 hover:shadow-md transition-all cursor-pointer group"
                    :ui="{
                      root: 'flex flex-col h-full',
                      header: 'p-4 pb-2.5',
                      body: 'p-4 pt-0 flex-1 flex flex-col justify-start',
                      footer: 'p-4 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between gap-2'
                    }"
                    @click="selectAlgorithmAndScroll(relAlg.id)"
                  >
                    <template #header>
                      <div class="flex items-start justify-between gap-2">
                        <UBadge
                          :color="getPolicyInfo(relAlg.minPolicy.id).badgeColor"
                          variant="subtle"
                          size="sm"
                        >
                          Level {{ relAlg.minPolicy.level }}: {{ relAlg.minPolicy.name }}
                        </UBadge>
                      </div>
                    </template>

                    <div class="space-y-1.5 flex-1">
                      <h5 class="font-bold text-sm text-neutral-900 group-hover:text-primary transition-colors leading-snug">
                        {{ relAlg.name }}
                      </h5>
                      <a
                        :href="relAlg.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="inline-flex items-center gap-1 font-mono text-xs text-neutral-500 hover:text-primary"
                        :aria-label="'View ' + relAlg.name + ' (' + relAlg.id + ') on EBI OLS4 (opens in new tab)'"
                        @click.stop
                      >
                        <span>{{ relAlg.id }}</span>
                        <UIcon name="i-lucide-external-link" class="size-3" />
                      </a>
                    </div>

                    <template #footer>
                      <div class="flex items-center justify-between w-full">
                        <span class="text-xs text-neutral-400">Click to select</span>
                        <UButton
                          size="xs"
                          color="primary"
                          variant="soft"
                          icon="i-lucide-arrow-up"
                          label="Select"
                          :aria-label="'Select algorithm ' + relAlg.name + ' and scroll to top'"
                          @click.stop="selectAlgorithmAndScroll(relAlg.id)"
                        />
                      </div>
                    </template>
                  </UCard>
                </div>

                <UAlert
                  v-else
                  color="info"
                  variant="subtle"
                  icon="i-lucide-info"
                  title="No Related KiSAO Algorithms"
                  description="No related algorithm terms were identified in the KiSAO substitution graph for this selection."
                />
              </div>
            </template>
          </UAccordion>
        </UCard>

        <!-- Default State: How KiSAO Suggestion Works -->
        <div v-else class="space-y-8">
          <!-- Guide Bento Grid -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-5">
            <UCard
              as="article"
              class="border border-neutral-200 shadow-sm flex flex-col h-full justify-between"
              :ui="{
                root: 'flex flex-col h-full',
                header: 'p-5 pb-3',
                body: 'p-5 pt-0 flex-1 flex flex-col justify-start',
                footer: 'p-5 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between'
              }"
            >
              <template #header>
                <div class="flex items-center gap-2.5">
                  <div class="size-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
                    <UIcon name="i-lucide-git-merge" class="size-5" />
                  </div>
                  <h3 class="font-bold text-base text-neutral-900">
                    KiSAO Graph Traversal
                  </h3>
                </div>
              </template>
              <div class="text-xs text-neutral-600 leading-relaxed space-y-2 flex-1">
                <p>
                  The Kinetic Simulation Algorithm Ontology organizes numerical methods by mathematical formalism, approximations, and scales.
                </p>
                <p>
                  When your model specifies an algorithm, BioSimulations maps ontology relationships to recommend equivalent tools.
                </p>
              </div>
              <template #footer>
                <span class="text-xs font-mono text-neutral-400">Ontology Level 1–8</span>
              </template>
            </UCard>

            <UCard
              as="article"
              class="border border-neutral-200 shadow-sm flex flex-col h-full justify-between"
              :ui="{
                root: 'flex flex-col h-full',
                header: 'p-5 pb-3',
                body: 'p-5 pt-0 flex-1 flex flex-col justify-start',
                footer: 'p-5 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between'
              }"
            >
              <template #header>
                <div class="flex items-center gap-2.5">
                  <div class="size-9 rounded-lg bg-emerald-500/10 text-emerald-600 flex items-center justify-center shrink-0">
                    <UIcon name="i-lucide-shield-check" class="size-5" />
                  </div>
                  <h3 class="font-bold text-base text-neutral-900">
                    Exact &amp; Alternative Solvers
                  </h3>
                </div>
              </template>
              <div class="text-xs text-neutral-600 leading-relaxed space-y-2 flex-1">
                <p>
                  Direct matches guarantee that the simulator implements the exact algorithm requested in the SED-ML experiment.
                </p>
                <p>
                  Alternative matches group substitute engines by substitution guarantees (e.g. same math equations or similar approximations).
                </p>
              </div>
              <template #footer>
                <span class="text-xs font-mono text-neutral-400">Multi-Solver Portability</span>
              </template>
            </UCard>

            <UCard
              as="article"
              class="border border-neutral-200 shadow-sm flex flex-col h-full justify-between"
              :ui="{
                root: 'flex flex-col h-full',
                header: 'p-5 pb-3',
                body: 'p-5 pt-0 flex-1 flex flex-col justify-start',
                footer: 'p-5 pt-3 mt-auto border-t border-neutral-100 flex items-center justify-between'
              }"
            >
              <template #header>
                <div class="flex items-center gap-2.5">
                  <div class="size-9 rounded-lg bg-indigo-500/10 text-indigo-600 flex items-center justify-center shrink-0">
                    <UIcon name="i-fluent-sparkle-20-filled" class="size-5" />
                  </div>
                  <h3 class="font-bold text-base text-neutral-900">
                    Instant Simulation Execution
                  </h3>
                </div>
              </template>
              <div class="text-xs text-neutral-600 leading-relaxed space-y-2 flex-1">
                <p>
                  Click "Run Simulation" on any suggested engine to immediately preload your chosen solver in the BioSimulations execution platform.
                </p>
                <p>
                  Upload your COMBINE/OMEX archive or public URL to execute with reproducible results.
                </p>
              </div>
              <template #footer>
                <span class="text-xs font-mono text-neutral-400">One-Click Handoff</span>
              </template>
            </UCard>
          </div>

          <!-- KiSAO Policy Levels Hierarchy Table -->
          <UCard class="border border-neutral-200 shadow-sm">
            <template #header>
              <div class="flex items-center justify-between gap-3">
                <div>
                  <h3 class="font-bold text-base sm:text-lg text-neutral-900 flex items-center gap-2">
                    <UIcon name="i-lucide-list-ordered" class="size-5 text-primary" />
                    KiSAO Algorithm Substitution Hierarchy
                  </h3>
                  <p class="text-xs text-neutral-500 mt-0.5">
                    Defined levels of substitution based on algorithm semantics and mathematical equivalence.
                  </p>
                </div>
                <UBadge color="primary" variant="subtle" size="sm">
                  Standardized Rules
                </UBadge>
              </div>
            </template>

            <div class="overflow-x-auto">
              <table class="w-full text-xs text-left">
                <thead class="bg-neutral-50 border-b border-neutral-200 text-neutral-700 font-semibold uppercase text-xs tracking-wider">
                  <tr>
                    <th class="py-3 px-4">Level</th>
                    <th class="py-3 px-4">Policy</th>
                    <th class="py-3 px-4">Meaning &amp; Simulation Guarantees</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-neutral-100 text-neutral-600">
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-emerald-700">Level 1</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Same Method</td>
                    <td class="py-3 px-4 leading-relaxed">Exact identical algorithm (e.g. CVODE executed by CVODE). Full numerical parity expected.</td>
                  </tr>
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-sky-700">Level 2</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Same Math</td>
                    <td class="py-3 px-4 leading-relaxed">Analytically equivalent formulation of identical equations (e.g. Gillespie direct vs Gibson-Bruck next reaction method).</td>
                  </tr>
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-indigo-700">Level 3</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Similar Approximations</td>
                    <td class="py-3 px-4 leading-relaxed">Comparable numerical order and approximation bounds (e.g. Runge-Kutta 4th order vs Cash-Karp).</td>
                  </tr>
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-purple-700">Level 4</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Distinct Approximations</td>
                    <td class="py-3 px-4 leading-relaxed">Alternative approximation strategy (e.g. tau-leaping approximations vs exact stochastic simulation).</td>
                  </tr>
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-amber-700">Level 5</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Distinct Scales</td>
                    <td class="py-3 px-4 leading-relaxed">Operates across different multi-scale spatial or temporal discretization levels.</td>
                  </tr>
                  <tr class="hover:bg-neutral-50/50">
                    <td class="py-3 px-4 font-mono font-semibold text-neutral-600">Level 8</td>
                    <td class="py-3 px-4 font-semibold text-neutral-900">Same Framework</td>
                    <td class="py-3 px-4 leading-relaxed">Belongs to the same broad biological modeling framework (e.g. discrete stochastic or continuous deterministic).</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </UCard>
        </div>
      </div>
    </div>
  </div>
</template>
