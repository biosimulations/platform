<script setup lang="ts">
  import {useIntersectionObserver} from '@vueuse/core'

  interface WorkflowFeature {
    title: string
    description: string
    selected: boolean
    duration: number,
    associated_multimedia: {
      type: string
      src: string
      alt: string
    }
  }

  const links = ref([
    {
      label: 'Run a Simulation',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg shadow-sm',
      to: '/simulations/run' as const,
      size: 'md' as const,
      color: 'primary' as const,
      variant: 'solid' as const,
      icon: 'i-gravity-ui-play-fill'
    },
    {
      label: 'Browse Simulations',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      to: '/simulations' as const,
      size: 'md' as const,
      color: 'primary' as const,
      variant: 'outline' as const,
      icon: 'i-lucide-search'
    },
    {
      label: 'Browse Simulators',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      to: '/simulators' as const,
      size: 'md' as const,
      color: 'primary' as const,
      variant: 'outline' as const,
      icon: 'i-lucide-cpu'
    },
    {
      label: 'Submit a Simulation',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      to: '/simulations/run' as const,
      size: 'md' as const,
      color: 'primary' as const,
      variant: 'outline' as const,
      icon: 'i-mynaui-send-solid'
    },
    {
      label: 'Visit the Docs',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      to: 'https://docs.biosimulations.org',
      target: '_blank' as const,
      size: 'md' as const,
      color: 'primary' as const,
      variant: 'outline' as const,
      icon: 'i-lucide-book-open',
      trailingIcon: 'i-lucide-external-link'
    }
  ])

  const partners = ref([
    {
      id: 1,
      name: 'Karr Lab',
      url: 'https://karrlab.org',
      src: '/images/collab_partners/image.png',
      alt: 'Karr Lab at Mount Sinai'
    },
    {
      id: 2,
      name: 'NIBIB',
      url: 'https://www.nibib.nih.gov',
      src: '/images/collab_partners/image-2.png',
      alt: 'National Institute of Biomedical Imaging and Bioengineering'
    },
    {
      id: 3,
      name: 'ModelDB',
      url: 'https://senselab.med.yale.edu/modeldb/',
      src: '/images/collab_partners/image-3.png',
      alt: 'ModelDB Computational Neuroscience'
    },
    {
      id: 4,
      name: 'NSF',
      url: 'https://www.nsf.gov',
      src: '/images/collab_partners/image-4.png',
      alt: 'National Science Foundation'
    },
    {
      id: 5,
      name: 'University of Washington',
      url: 'https://www.washington.edu',
      src: '/images/collab_partners/image-5.png',
      alt: 'University of Washington'
    },
    {
      id: 6,
      name: 'UConn Health',
      url: 'https://health.uconn.edu',
      src: '/images/collab_partners/image-6.png',
      alt: 'UConn Health Center for Cell Analysis & Modeling'
    },
    {
      id: 7,
      name: 'CRBM',
      url: 'https://reproduciblebiomedicalmodeling.org',
      src: '/images/collab_partners/image-7.png',
      alt: 'Center for Reproducible Biomedical Modeling'
    },
    {
      id: 8,
      name: 'BioModels',
      url: 'https://www.ebi.ac.uk/biomodels/',
      src: '/images/collab_partners/image-8.png',
      alt: 'BioModels Database'
    },
    {
      id: 9,
      name: 'BiGG Models',
      url: 'http://bigg.ucsd.edu/',
      src: '/images/collab_partners/image-9.png',
      alt: 'BiGG Models Knowledgebase'
    },
    {
      id: 10,
      name: 'Mount Sinai',
      url: 'https://icahn.mssm.edu',
      src: '/images/collab_partners/image-10.png',
      alt: 'Icahn School of Medicine at Mount Sinai'
    },
    {
      id: 11,
      name: 'JWS Online',
      url: 'https://jjj.bio.vu.nl/',
      src: '/images/collab_partners/image-11.png',
      alt: 'JWS Online Cellular Systems Modeling'
    },
    {
      id: 12,
      name: 'RBA',
      url: 'https://rba.inrae.fr/',
      src: '/images/collab_partners/image-12.png',
      alt: 'Resource Balance Analysis'
    },
    {
      id: 13,
      name: 'NIH',
      url: 'https://www.nih.gov',
      src: '/images/collab_partners/image-13.png',
      alt: 'National Institutes of Health'
    }
  ])

  const built_using = ref([
    {
      id: 1,
      name: 'COMBINE',
      url: 'https://combine-org.github.io/',
      src: '/images/built_using/image.png',
      alt: 'COMBINE Standards'
    },
    {
      id: 2,
      name: 'COPASI',
      url: 'https://copasi.org/',
      src: '/images/built_using/image-2.png',
      alt: 'COPASI Biochemical Network Simulator'
    },
    {
      id: 3,
      name: 'GitHub',
      url: 'https://github.com/biosimulations',
      src: '/images/built_using/image-3.png',
      alt: 'GitHub'
    },
    {
      id: 4,
      name: 'Modeling Tools Group',
      url: 'https://combine-org.github.io/',
      src: '/images/built_using/image-4.png',
      alt: 'Modeling Tools Group'
    },
    {
      id: 5,
      name: 'NestJS',
      url: 'https://nestjs.com/',
      src: '/images/built_using/image-5.png',
      alt: 'NestJS Framework'
    },
    {
      id: 6,
      name: 'Netlify',
      url: 'https://www.netlify.com/',
      src: '/images/built_using/image-6.png',
      alt: 'Netlify Cloud Platform'
    },
    {
      id: 7,
      name: 'BioSimulators',
      url: 'https://biosimulators.org/',
      src: '/images/built_using/image-7.png',
      alt: 'BioSimulators Registry'
    },
    {
      id: 8,
      name: 'Font Awesome',
      url: 'https://fontawesome.com/',
      src: '/images/built_using/image-8.png',
      alt: 'Font Awesome'
    },
    {
      id: 9,
      name: 'BioSimulations',
      url: 'https://biosimulations.org/',
      src: '/images/built_using/image-9.png',
      alt: 'BioSimulations Ecosystem'
    },
    {
      id: 10,
      name: 'Kubernetes',
      url: 'https://kubernetes.io/',
      src: '/images/built_using/image-10.png',
      alt: 'Kubernetes Container Orchestration'
    },
    {
      id: 11,
      name: 'Docker',
      url: 'https://www.docker.com/',
      src: '/images/built_using/image-11.png',
      alt: 'Docker Container Engine'
    },
    {
      id: 12,
      name: 'OpenAPI',
      url: 'https://swagger.io/',
      src: '/images/built_using/image-12.png',
      alt: 'OpenAPI Swagger Specifications'
    },
    {
      id: 13,
      name: 'Material Design',
      url: 'https://m3.material.io/',
      src: '/images/built_using/image-13.png',
      alt: 'Material Design'
    },
    {
      id: 14,
      name: 'Auth0',
      url: 'https://auth0.com/',
      src: '/images/built_using/image-14.png',
      alt: 'Auth0 Identity Platform'
    },
    {
      id: 15,
      name: 'MongoDB',
      url: 'https://www.mongodb.com/',
      src: '/images/built_using/image-15.png',
      alt: 'MongoDB Database'
    },
    {
      id: 16,
      name: 'Plotly',
      url: 'https://plotly.com/',
      src: '/images/built_using/image-16.png',
      alt: 'Plotly Scientific Visualizations'
    },
    {
      id: 17,
      name: 'Angular',
      url: 'https://angular.dev/',
      src: '/images/built_using/image-17.png',
      alt: 'Angular Web Framework'
    }
  ])
  const workflow_features = ref<WorkflowFeature[]>([
    {
      title: 'Run a Simulation',
      description: 'Execute custom simulations online in containerized cloud environments',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image.jpg',
        alt: 'Illustration of running a simulation online'
      }
    },
    {
      title: 'Discover & Compare Simulators',
      description: 'Find simulators and compare them to find the right one for your use case',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image.jpg',
        alt: 'Illustration of discovering and comparing simulators'
      }
    },
    {
      title: 'Browse Simulations',
      description: 'Browse and search published, peer-reviewed simulations and biomodels in the database',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-2.jpg',
        alt: 'Illustration of browsing published simulations'
      }
    },
    {
      title: 'Submit a Simulation',
      description: 'Upload and publish your own simulation experiments, models, and COMBINE archives',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-3.jpg',
        alt: 'Illustration of submitting a simulation experiment'
      }
    },
    {
      title: 'Convert a File',
      description: 'Convert and validate simulation files, SED-ML specifications, and model formats',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-4.jpg',
        alt: 'Illustration of file conversion and validation'
      }
    },
    {
      title: 'REST API',
      description: 'Programmatically interact with BioSimulations using our automated OpenAPI REST endpoints',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-5.jpg',
        alt: 'Illustration of BioSimulations REST API integration'
      }
    },
    {
      title: 'Documentation',
      description: 'Explore comprehensive guides, tutorials, and community standards documentation',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-6.jpg',
        alt: 'Illustration of BioSimulations documentation and user guides'
      }
    }
  ])

  const workflow_container = ref(null)
  const current_workflow_feature = ref<WorkflowFeature | null>(null)

  useIntersectionObserver(
    workflow_container,
    ([entry]) => {
      if (entry && entry.isIntersecting) {
        workflow_features.value[0]!.selected = true
        current_workflow_feature.value = workflow_features.value[0]!
      } else {
        if (current_workflow_feature.value) {
          current_workflow_feature.value.selected = false
          current_workflow_feature.value = null
        }
      }
    },
    {}
  )

  // Every (progress_interval / 1000) seconds or on click, switch to the next active WorkflowFeature (or loop back to beginning if at end). Hovering over the feature will pause the timer
  // Call this function when animation for the template of the current_workflow_feature progress bar is complete
  function switch_workflow_feature(index_override: number | undefined = undefined) {
    const current_index = workflow_features.value.findIndex(feature => feature.selected)
    const next_index = index_override !== undefined ? index_override : (current_index + 1) % (workflow_features.value as WorkflowFeature[]).length
    const next_workflow_feature = (workflow_features.value as WorkflowFeature[])[next_index]!

    if (current_workflow_feature.value) {
      current_workflow_feature.value.selected = false
    }

    next_workflow_feature.selected = true
    current_workflow_feature.value = next_workflow_feature
  }

  // --- Props / Variables ---
  const TILE_SIZE = 20;
  const TILE_GAP = 10; // Adjusted for visual spacing, STEP is the logic
  const STEP = TILE_SIZE + TILE_GAP;
  const TILE_RADIUS = 4;
  const BASE_GRAY = "#f7f7f7";
  const BLUE_COLOR = "#2b8cff";

  const MAX_ACTIVE_TILES = 20;
  const HOLD_TIME_MS = 1000;
  const FADE_DURATION = 800;
  const _STAGGER_MAX = 1200;
  const OPACITY_SEED = 1337;

  // --- State ---
  const canvas = ref<HTMLCanvasElement | null>(null);
  let ctx: CanvasRenderingContext2D | null = null;
  let offscreenCanvas: HTMLCanvasElement | null = null;
  let animationFrame: number;

  interface ActiveTile {
    x: number;
    y: number;
    start: number;
    opacity: number;
  }
  let activeTiles: ActiveTile[] = [];

  // --- 1. Seeded Random Utility ---
  // Returns a deterministic pseudo-random number based on x, y, and seed
  const getTileOpacity = (x: number, y: number) => {
    const n = Math.sin(x * 12.9898 + y * 78.233 + OPACITY_SEED) * 43758.5453123;
    const random = n - Math.floor(n);
    return Math.round(random * 10) / 10; // 0.1 step increments
  };

  // --- 2. Grid Caching (Corrected for Scaling) ---
  const drawGridCache = (width: number, height: number, dpr: number) => {
    offscreenCanvas = document.createElement('canvas');
    offscreenCanvas.width = width * dpr;
    offscreenCanvas.height = height * dpr;

    const oCtx = offscreenCanvas.getContext('2d');
    if (!oCtx) return;

    oCtx.scale(dpr, dpr); // Scale the internal drawing of the cache
    oCtx.fillStyle = BASE_GRAY;

    for (let x = 0; x < width; x += STEP) {
      for (let y = 0; y < height; y += STEP) {
        const masterOpacity = getTileOpacity(x, y);
        oCtx.globalAlpha = masterOpacity;
        oCtx.beginPath();
        oCtx.roundRect(x, y, TILE_SIZE, TILE_SIZE, TILE_RADIUS);
        oCtx.fill();
      }
    }
  };

  // --- 3. Animation Loop ---
  const animate = (time: number) => {
    if (!ctx || !canvas.value || !offscreenCanvas) return;

    const width = window.innerWidth;
    const height = window.innerHeight;

    // 1. Clear the canvas
    // Note: Since we use ctx.scale(dpr, dpr), (0,0,width,height)
    // correctly covers the logical screen area.
    ctx.clearRect(0, 0, width, height);

    // 2. Draw the Static Gray Grid Cache
    ctx.drawImage(offscreenCanvas, 0, 0, width, height);

    // 3. Spawn Logic: Add a new blue tile if we're under the limit
    if (activeTiles.length < MAX_ACTIVE_TILES && Math.random() < 0.1) {
      // Pick a random grid intersection
      const col = Math.floor(Math.random() * (width / STEP));
      const row = Math.floor(Math.random() * (height / STEP));
      const x = col * STEP;
      const y = row * STEP;

      // Check if this tile is already active to avoid stacking
      const exists = activeTiles.some(t => t.x === x && t.y === y);

      if (!exists) {
        activeTiles.push({
          x,
          y,
          start: time,
          opacity: getTileOpacity(x, y) // Get the same seed-based opacity
        });
      }
    }

    // 4. Draw & Update Active Blue Tiles
    const totalCycle = FADE_DURATION + HOLD_TIME_MS + FADE_DURATION;

    activeTiles = activeTiles.filter((tile: any) => {
      const elapsed = time - tile.start;

      // Remove tile if animation cycle is complete
      if (elapsed > totalCycle) return false;

      // Calculate Alpha based on the phase (In, Hold, or Out)
      let progressAlpha = 0;
      if (elapsed < FADE_DURATION) {
        // Phase 1: Fade In
        progressAlpha = elapsed / FADE_DURATION;
      } else if (elapsed < FADE_DURATION + HOLD_TIME_MS) {
        // Phase 2: Hold
        progressAlpha = 1;
      } else {
        // Phase 3: Fade Out
        const fadeOutElapsed = elapsed - (FADE_DURATION + HOLD_TIME_MS);
        progressAlpha = 1 - (fadeOutElapsed / FADE_DURATION);
      }

      // Draw the blue square
      ctx!.save();
      // Multiply the animation alpha by the master seed opacity
      ctx!.globalAlpha = progressAlpha * tile.opacity;
      ctx!.fillStyle = BLUE_COLOR;
      ctx!.beginPath();
      ctx!.roundRect(tile.x, tile.y, TILE_SIZE, TILE_SIZE, TILE_RADIUS);
      ctx!.fill();
      ctx!.restore();

      return true;
    });

    animationFrame = requestAnimationFrame(animate);
  };

  let resizeHandler: (() => void) | null = null;

  // --- 4. Lifecycle & Resize ---
  const init = () => {
    if (!canvas.value) return;
    ctx = canvas.value.getContext('2d');

    const resize = () => {
      if (!canvas.value) return;
      const dpr = window.devicePixelRatio || 1;
      const width = window.innerWidth;
      const height = window.innerHeight;

      // 1. Set physical buffer size
      canvas.value.width = width * dpr;
      canvas.value.height = height * dpr;

      // 2. Set CSS display size
      canvas.value.style.width = `${width}px`;
      canvas.value.style.height = `${height}px`;

      // 3. Reset and then apply scale
      // Note: Setting width/height resets the context,
      // but we apply the scale here for the 'active' drawing
      ctx?.scale(dpr, dpr);

      // 4. Generate the cache at physical size
      drawGridCache(width, height, dpr);
    };

    resizeHandler = resize;
    window.addEventListener('resize', resizeHandler);
    resize();

    // Accessibility check
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion) {
      // We draw using the logical width/height to correct the scale
      ctx?.drawImage(offscreenCanvas!, 0, 0, window.innerWidth, window.innerHeight);
    } else {
      animationFrame = requestAnimationFrame(animate);
    }
  };

  onMounted(() => {
    init();
  });

  onUnmounted(() => {
    cancelAnimationFrame(animationFrame);
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler);
    }
  });
</script>
<template>
  <div class="flex flex-col">
    <div class="relative overflow-hidden">
      <UPageHero
        class="z-2"
        orientation="vertical"
        :links="links"
        :ui="{
          headline: 'w-full mb-[40px]',
          wrapper: 'w-full flex flex-col items-center gap-[40px]',
          header: 'w-full flex flex-col items-center gap-[8px]',
          container: 'max-w-[1200px] w-full px-4 !lg:px-0 !pt-[125px] !pb-0 gap-y-[75px] mx-auto',
          description: 'mt-0',
          links: 'flex flex-wrap gap-x-[16px] gap-y-[8px]',
          footer: 'mt-0'
        }"
      >
        <template #headline>
          <UBadge
            variant="subtle"
            size="lg"
            color="secondary"
            class="rounded-full gap-2 px-3 py-1.5 cursor-pointer text-sm font-normal"
            to="/biosim-db"
          >
            <UIcon name="i-mage-star-fill" class="size-4 text-secondary-500" />
            <span>Meet our new Reproducibility Portal: a more user-friendly way to interact with the BioSim database!</span>
            <UIcon name="i-lucide-arrow-right" class="size-4" />
          </UBadge>
        </template>
        <template #title>
          <h1 class="text-4xl font-bold m-0 tracking-normal">Powerful biomodel simulations and tools <span class="text-blue-500">at your fingertips</span></h1>
        </template>
        <template #description>
          <p class="text-lg">Generate, share, and re-use biomodels, simulations, results, and visualizations like never before with our free registry of biosimulation tools.</p>
        </template>
        <UPageCard class="w-full h-[500px] rounded-lg" variant="subtle">
          <div class="placeholder w-full h-full bg-black rounded-lg" />
        </UPageCard>
      </UPageHero>
<!--      <canvas ref="canvas" id="hero_bg" class="absolute top-0 left-0 z-1 w-full h-full" />-->
      <div id="bottom_fade_gradient" class="absolute top-0 left-0 w-full h-full -z-1"></div>
      <canvas
        ref="canvas"
        class="absolute top-0 left-0 w-full h-full opacity-50 -z-10 pointer-events-none"
      />
    </div>

    <section id="marquees" class="w-full max-w-[1200px] px-6 mx-auto flex flex-col mt-[100px] gap-[125px]">
      <div class="w-full flex flex-col items-center gap-6">
        <UPageLogos
          class="w-full"
          title="In Collaboration With"
          :marquee="{ pauseOnHover: true, overlay: false, orientation: 'horizontal' }"
          :ui="{ title: 'text-lg text-center font-bold text-highlighted', logos: 'mt-6' }"
        >
          <NuxtLink v-for="partner of partners" :key="partner.id" :to="partner.url" target="_blank" rel="noopener noreferrer">
            <img class="max-h-[40px] max-w-[250px] w-full h-full" :alt="partner.alt" :src="partner.src"/>
          </NuxtLink>
        </UPageLogos>
        <p class="text-sm opacity-50 text-center leading-7">BioSimulations was developed by the Center for Reproducible Biomedical Modeling , the Karr Lab at the Icahn School of Medicine at Mount Sinai , and the Center for Cell Analysis & Modeling at the University of Connecticut Health Center  with support from the National Institutes of Health and the National Science Foundation.</p>
      </div>

      <div class="w-full flex flex-col items-center gap-6">
        <UPageLogos
          class="w-full"
          title="Built Using These Awesome Projects & Platforms"
          :marquee="{ pauseOnHover: true, reverse: true, overlay: false, orientation: 'horizontal' }"
          :ui="{ title: 'text-lg text-center font-bold text-highlighted', logos: 'mt-6' }"
        >
          <NuxtLink v-for="built_item of built_using" :key="built_item.id" :to="built_item.url" target="_blank" rel="noopener noreferrer">
            <img class="max-h-[40px] max-w-[250px] w-full h-full" :alt="built_item.alt" :src="built_item.src"/>
          </NuxtLink>
        </UPageLogos>
        <p class="text-sm opacity-50 text-center leading-7">BioSimulations is implemented using several open-source tools and cloud platforms. The models, simulations, and visualizations are available under the licenses specified for each resource. The code is openly available under the MIT license. The simulation tools provided are available under the licenses specified for each simulation tool at BioSimulators.</p>
      </div>
    </section>

    <UPageSection
      id="revolutionary_platform"
      class="w-full max-w-[1200px] px-6 mx-auto py-12 mt-24"
      :ui="{
        root: 'relative isolate',
        container: 'relative overflow-hidden max-w-[1200px]! rounded-3xl border border-blue-200/60 dark:border-blue-900/40 bg-linear-to-r from-blue-500/10 via-cyan-500/5 to-amber-500/10 !p-8 sm:!p-10 lg:!p-12 shadow-xs backdrop-blur-sm flex flex-col gap-8!',
        wrapper: 'text-center',
        headline: 'mb-3 text-center',
        title: 'text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-slate-900 dark:text-white text-center',
        description: 'text-base sm:text-lg text-slate-600 dark:text-slate-300 leading-relaxed text-center'
      }"
    >
      <template #headline>
        <UBadge color="primary" variant="subtle" size="lg" class="rounded-full px-2.5">
          <UIcon name="i-fluent-sparkle-20-filled" class="size-3.5 mr-1" />
          What is BioSimulations?
        </UBadge>
      </template>

      <template #title>
        A Revolutionary Platform
      </template>

      <template #description>
        BioSimulations is <em>the</em> free platform for discovering and comparing simulators, sharing and re-using biomodels, running simulations, and browsing/sharing/visualizing simulation results. With simulators and simulations on one platform, researchers have access to streamlined workflows to accelerate their discoveries.
      </template>

      <UPageGrid class="grid-cols-1 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-2 gap-6">
        <UPageCard
          title="Simulators"
          description="Access a registry of biosimulation tools encompassing diverse frameworks, algorithms, and model formats curated to streamline the simulation process. The registry, powered by conventions like SED-ML, offers readily available Python packages, command-line programs, and Docker images for consistent execution."
          variant="subtle"
          class="bg-white/80 dark:bg-slate-900/80 backdrop-blur-sm border border-slate-200/60 dark:border-slate-800/60 shadow-xs"
          :ui="{
            title: 'text-xl font-bold text-slate-900 dark:text-white',
            description: 'text-slate-600 dark:text-slate-300 leading-relaxed text-sm',
            footer: 'mt-6'
          }"
        >
          <template #footer>
            <div class="flex flex-wrap items-center gap-3">
              <UButton
                color="primary"
                size="md"
                icon="i-lucide-rocket"
                to="/simulators/get-started"
                label="Get Started"
                class="font-semibold shadow-xs"
              />
              <UButton
                color="neutral"
                variant="outline"
                size="md"
                icon="i-lucide-search"
                to="/simulators"
                label="Browse Simulators"
                class="font-medium bg-white/60 dark:bg-slate-800/60"
              />
            </div>
          </template>
        </UPageCard>

        <UPageCard
          title="Simulations"
          description="Access a wide range of frameworks (e.g., logical, Flux-Balance Analysis (FBA), continuous kinetic, discrete kinetic), simulation algorithms (e.g., FBA, SSA), model formats (e.g., SBML), and tools (e.g., COBRApy, COPASI, tellurium) and connect with a community of others supercharging their bio-data processing."
          variant="subtle"
          class="bg-white/80 dark:bg-slate-900/80 backdrop-blur-sm border border-slate-200/60 dark:border-slate-800/60 shadow-xs"
          :ui="{
            title: 'text-xl font-bold text-slate-900 dark:text-white',
            description: 'text-slate-600 dark:text-slate-300 leading-relaxed text-sm',
            footer: 'mt-6'
          }"
        >
          <template #footer>
            <div class="flex flex-wrap items-center gap-3">
              <UButton
                color="primary"
                size="md"
                icon="i-lucide-sparkles"
                to="/simulations/run"
                label="Run a Simulation"
                class="font-semibold shadow-xs"
              />
              <UButton
                color="neutral"
                variant="outline"
                size="md"
                icon="i-lucide-search"
                to="/simulations"
                label="Browse Simulation Runs"
                class="font-medium bg-white/60 dark:bg-slate-800/60"
              />
            </div>
          </template>
        </UPageCard>
      </UPageGrid>
    </UPageSection>

    <UPageSection
      id="workflow"
      class="w-full"
      title="Your Workflow, Your Way"
      description="Starting from scratch? BioSimulations offers a nuts-to-bolts pipeline. Ready to import and upgrade some data? BioSimulations has extensive documentation to make supercharging your bio data easy."
      :ui="{
        container: 'max-w-[1200px]',
        header: 'text-center md:text-left',
        title: 'text-3xl font-bold',
        description: 'text-lg'
      }"
    >
      <div class="w-full flex flex-col md:flex-col-reverse lg:flex-row items-center lg:items-stretch justify-center lg:justify-between gap-6" ref="workflow_container">
        <TransitionGroup class="w-full flex flex-col gap-4" name="workflow_features" tag="div">
          <div tabindex="0" role="button" :aria-label="workflow_feature.title" :id="`workflow_feature_${index}`" :class="`workflow_feature py-1 cursor-pointer rounded-xl group flex flex-col gap-4 w-full ${workflow_feature.selected ? 'bg-gray-50 p-4!' : 'bg-transparent'}`" v-for="(workflow_feature, index) of workflow_features" :key="workflow_feature.title" @keydown.enter.prevent="switch_workflow_feature(index)" @keydown.space.prevent="switch_workflow_feature(index)" @click="switch_workflow_feature(index)">
            <div class="w-full flex flex-col">
              <p class="text-lg" :class="workflow_feature.selected ? 'text-black' : 'opacity-50 group-hover:opacity-100'"><strong>{{workflow_feature.title}}</strong></p>
              <p v-if="workflow_feature.selected">{{workflow_feature.description}}</p>
            </div>
            <!-- Static multimedia slot for mobile; renders the `associated_multimedia` of the current active WorkflowFeature -->
            <div v-if="workflow_feature.selected" id="mobile_multimedia_wrapper" class="w-full min-h-[100px] shadow-2xl overflow-hidden rounded-xl block md:hidden">
              <img class="object-cover" v-if="current_workflow_feature && current_workflow_feature.associated_multimedia.type == 'image'" :src="current_workflow_feature.associated_multimedia.src" :alt="current_workflow_feature.associated_multimedia.alt">
            </div>
            <UProgress
              v-if="workflow_feature.selected"
              class="w-full"
              size="sm"
              color="primary"
              :model-value="0"
              :ui="{
                base: 'h-[6px] bg-gray-100 rounded-full overflow-hidden relative',
                indicator: 'progress_bar bg-blue-500 rounded-full group-hover:[--play-state:paused] !transform-none'
              }"
              :style="{'--duration': `${workflow_feature.duration}ms`}"
              @animationend="switch_workflow_feature()"
            />
          </div>
        </TransitionGroup>

        <!-- Static multimedia slot; renders the `associated_multimedia` of the current active WorkflowFeature -->
        <div id="multimedia_wrapper" class="w-full min-h-[100px] shadow-2xl overflow-hidden rounded-xl hidden md:block" data-aos="fade-up" data-aos-duration="1000">
          <img class="w-full h-full object-cover object-center block" v-if="current_workflow_feature && current_workflow_feature.associated_multimedia.type == 'image'" :src="current_workflow_feature.associated_multimedia.src" :alt="current_workflow_feature.associated_multimedia.alt">
        </div>
      </div>
    </UPageSection>
  </div>
</template>
<style>
  #bottom_fade_gradient {
    background: linear-gradient(to bottom, transparent 75%, white);
  }

  .workflow_feature {
    transition: 0.25s all;

    p {
      transition: 0.25s all;
    }

    .progress_bar {
      --duration: 3000ms;
      animation-name: fill-progress;
      animation-duration: var(--duration);
      animation-iteration-count: 1;
      animation-fill-mode: forwards;
      animation-timing-function: linear;

      /*
      --- ATTENTION ---
      Ignore intellisense for undefined `--play-state` variable below: --play-state is intentionally not defined as CSS variable here. Do not add it to the CSS.

      Reason:
        - Tailwind is setting `--play-state` automatically at a scope outside of the .progress_bar class (but not explicitly defined in the stylesheet)
          due to the group-hover:[...] property on the .progress_bar element, at a level where .progress_bar can still inherit the value.
        - If we locally scope --play-state here, we override where Tailwind is placing it, essentially blocking the behavior we should get from Tailwind.

      Conclusion: Although counterintuitive and annoying to see the linter complain about a missing variable, this is intentional and should not be changed.
      --- END ---
      */

      /* Uncomment the below line to enable animation pausing on hover */
      /* animation-play-state: var(--play-state, running); */
    }
  }

  @keyframes fill-progress {
    from {
      width: 0;
    }

    to {
      width: 100%;
    }
  }
</style>
