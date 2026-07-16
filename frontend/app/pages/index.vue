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
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      size: 'md' as const,
      color: 'info' as const,
      leadingIcon: 'i-gravity-ui-play-fill' // w-4
    },
    {
      label: 'Compare Simulators',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      size: 'md' as const,
      color: 'warning' as const,
      leadingIcon: 'i-lucide-cpu' // w-4
    },
    {
      label: 'Browse Simulations',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'outline' as const,
      leadingIcon: 'i-lucide-search' // w-4
    },
    {
      label: 'Submit a Simulation',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'outline' as const,
      leadingIcon: 'i-mynaui-send-solid' // w-4
    },
    {
      label: 'Visit the Docs',
      class: 'cursor-pointer gap-2 text-base font-normal rounded-lg',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'outline' as const,
      leadingIcon: 'i-lucide-book-open' // w-4
    }
  ])
  const cta_links = ref([
    {
      label: 'Run a simulation',
      class: 'cursor-pointer !gap-2 text-base font-semibold rounded-lg !text-white !border-white !bg-white/0 !border-1',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'subtle' as const,
      leadingIcon: 'i-gravity-ui-play-fill' // w-4
    },
    {
      label: 'Compare Simulators',
      class: 'cursor-pointer !gap-2 text-base font-semibold rounded-lg !text-white !border-white !bg-white/0 !border-1',
      size: 'md' as const,
      color: 'neutral' as const,
      leadingIcon: 'i-lucide-cpu' // w-4
    },
    {
      label: 'Browse simulations',
      class: 'cursor-pointer !gap-2 text-base font-semibold rounded-lg !text-white !border-white !bg-white/0 !border-1',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'subtle' as const,
      leadingIcon: 'i-lucide-search' // w-4
    },
    {
      label: 'Submit a simulation',
      class: 'cursor-pointer !gap-2 text-base font-semibold rounded-lg !text-white !border-white !bg-white/0 !border-1',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'subtle' as const,
      leadingIcon: 'i-mynaui-send-solid' // w-4
    },
    {
      label: 'Visit the docs',
      class: 'cursor-pointer !gap-2 text-base font-semibold rounded-lg !text-white !border-white !bg-white/0 !border-1',
      size: 'md' as const,
      color: 'neutral' as const,
      variant: 'subtle' as const,
      leadingIcon: 'i-lucide-book-open' // w-4
    }
  ])
  // TODO: Update names and urls, add numerical sequential ids
  const partners = ref([
    {
      id: 1,
      url: 'https://google.com',
      src: '/images/collab_partners/image.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 2,
      url: 'https://google.com',
      src: '/images/collab_partners/image-2.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 3,
      url: 'https://google.com',
      src: '/images/collab_partners/image-3.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 4,
      url: 'https://google.com',
      src: '/images/collab_partners/image-4.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 5,
      url: 'https://google.com',
      src: '/images/collab_partners/image-5.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 6,
      url: 'https://google.com',
      src: '/images/collab_partners/image-6.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 7,
      url: 'https://google.com',
      src: '/images/collab_partners/image-7.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 8,
      url: 'https://google.com',
      src: '/images/collab_partners/image-8.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 9,
      url: 'https://google.com',
      src: '/images/collab_partners/image-9.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 10,
      url: 'https://google.com',
      src: '/images/collab_partners/image-10.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 11,
      url: 'https://google.com',
      src: '/images/collab_partners/image-11.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 12,
      url: 'https://google.com',
      src: '/images/collab_partners/image-12.png',
      alt: 'Partner Logo or Image'
    },
    {
      id: 13,
      url: 'https://google.com',
      src: '/images/collab_partners/image-13.png',
      alt: 'Partner Logo or Image'
    }
  ])

  // TODO: Update names and urls, add numerical sequential ids starting from 1
  const built_using = ref([
    {
      id: 1,
      url: 'https://google.com',
      src: '/images/built_using/image.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 2,
      url: 'https://google.com',
      src: '/images/built_using/image-2.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 3,
      url: 'https://google.com',
      src: '/images/built_using/image-3.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 4,
      url: 'https://google.com',
      src: '/images/built_using/image-4.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 5,
      url: 'https://google.com',
      src: '/images/built_using/image-5.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 6,
      url: 'https://google.com',
      src: '/images/built_using/image-6.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 7,
      url: 'https://google.com',
      src: '/images/built_using/image-7.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 8,
      url: 'https://google.com',
      src: '/images/built_using/image-8.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 9,
      url: 'https://google.com',
      src: '/images/built_using/image-9.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 10,
      url: 'https://google.com',
      src: '/images/built_using/image-10.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 11,
      url: 'https://google.com',
      src: '/images/built_using/image-11.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 12,
      url: 'https://google.com',
      src: '/images/built_using/image-12.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 13,
      url: 'https://google.com',
      src: '/images/built_using/image-13.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 14,
      url: 'https://google.com',
      src: '/images/built_using/image-14.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 15,
      url: 'https://google.com',
      src: '/images/built_using/image-15.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 16,
      url: 'https://google.com',
      src: '/images/built_using/image-16.png',
      alt: 'Logo or Image of Technology/Platform Used'
    },
    {
      id: 17,
      url: 'https://google.com',
      src: '/images/built_using/image-17.png',
      alt: 'Logo or Image of Technology/Platform Used'
    }
  ])
  const workflow_features = ref<WorkflowFeature[]>([
    {
      title: 'Run a Simulation',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image.jpg',
        alt: ''
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
        alt: ''
      }
    },
    {
      title: 'Browse Simulations',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-2.jpg',
        alt: ''
      }
    },
    {
      title: 'Submit a Simulation',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-3.jpg',
        alt: ''
      }
    },
    {
      title: 'Convert a File',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-4.jpg',
        alt: ''
      }
    },
    {
      title: 'REST API',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-5.jpg',
        alt: ''
      }
    },
    {
      title: 'Documentation',
      description: 'Execute custom simulations online',
      selected: false,
      duration: 10000,
      associated_multimedia: {
        type: 'image',
        src: '/images/workflow_multimedia/image-6.jpg',
        alt: ''
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

    window.addEventListener('resize', resize);
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
    window.removeEventListener('resize', () => {});
  });
</script>
<template>
  <div class="flex flex-col gap-[125px]">
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
          <UAlert
            class="w-full p-2 flex flex-col lg:flex-row items-center gap-[16px] justify-center lg:justify-between"
            title="Meet our new Reproducibility Portal: a more user-friendly way to interact with the BioSim database!"
            icon="i-mage-star-fill"
            orientation="horizontal"
            variant="subtle"
            color="secondary"
            :ui="{
              title: 'text-sm text-center md:text-left font-normal',
              icon: 'size-5'
            }"
            :actions="[
              {
                label: 'Let\'s Go!',
                size: 'sm',
                class: 'cursor-pointer',
                icon: 'i-lucide-external-link',
                color: 'secondary',
              }
            ]">
          </UAlert>
        </template>
        <template #title>
          <h1 class="text-4xl font-bold m-0 tracking-normal">Powerful biomodel simulations and tools <span class="text-blue-500">at your fingertips</span></h1>
        </template>
        <template #description>
          <p class="text-lg">Generate, share, and re-use biomodels, simulations, results, and visualizations like never before with our free registry of biosimulation tools.</p>
        </template>
        <div class="placeholder w-full h-[500px] bg-black rounded-lg"></div>
  <!--      <img
          src="/blocks/image4.png"
          alt="App screenshot"
          class="rounded-lg shadow-2xl ring ring-default"
        />-->
      </UPageHero>
<!--      <canvas ref="canvas" id="hero_bg" class="absolute top-0 left-0 z-1 w-full h-full" />-->
      <div id="bottom_fade_gradient" class="absolute top-0 left-0 w-full h-full -z-1"></div>
      <canvas
        ref="canvas"
        class="absolute top-0 left-0 w-full h-full opacity-50 -z-10 pointer-events-none"
      />
    </div>

    <section id="marquees" class="w-full max-w-[1200px] px-6 mx-auto flex flex-col gap-[125px]">
      <div class="w-full flex flex-col items-center gap-6">
        <p class="text-lg text-center"><strong>In Collaboration With</strong></p>
        <UMarquee class="w-full overflow-hidden" pause-on-hover :overlay="false" orientation="horizontal">
          <NuxtLink v-for="partner of partners" :key="partner.id" :to="partner.url" target="_blank">
            <img class="max-h-[40px] max-w-[250px] w-full h-full" :alt="partner.alt" :src="partner.src"/>
          </NuxtLink>
        </UMarquee>
        <p class="text-sm opacity-50 text-center leading-7">BioSimulations was developed by the Center for Reproducible Biomedical Modeling , the Karr Lab at the Icahn School of Medicine at Mount Sinai , and the Center for Cell Analysis & Modeling at the University of Connecticut Health Center  with support from the National Institutes of Health and the National Science Foundation.</p>
      </div>

      <div class="w-full flex flex-col items-center gap-6">
        <p class="text-lg text-center"><strong>Built Using These Awesome Projects & Platforms</strong></p>
        <UMarquee class="w-full overflow-hidden" pause-on-hover reverse :overlay="false" orientation="horizontal">
          <NuxtLink v-for="built_item of built_using" :key="built_item.id" :to="built_item.url" target="_blank">
            <img class="max-h-[40px] max-w-[250px] w-full h-full" :alt="built_item.alt" :src="built_item.src"/>
          </NuxtLink>
        </UMarquee>
        <p class="text-sm opacity-50 text-center leading-7">BioSimulations is implemented using several open-source tools and cloud platforms. The models, simulations, and visualizations are available under the licenses specified for each resource. The code is openly available under the MIT license. The simulation tools provided are available under the licenses specified for each simulation tool at BioSimulators.</p>
      </div>
    </section>
    <section id="revolutionary_platform" class="w-full relative px-6 py-[150px] text-white flex items-center text-center md:text-left justify-center">
      <div id="revolutionary_content" class="w-full max-w-[1200px] mx-auto flex flex-col gap-2 z-4">
        <small class="uppercase tracking-widest text-[0.8rem] font-semibold">what is biosimulations?</small>
        <h2 class="font-bold text-3xl">A Revolutionary Platform</h2>
        <p class="leading-7 text-lg">BioSimulations is <em>the</em> free platform for discovering and comparing simulators, sharing and re-using biomodels, running simulations, and browsing/sharing/visualizing simulation results. With simulators and simulations on one platform, researchers have access to streamlined workflows to accelerate their discoveries.</p>
        <br>
        <small class="uppercase tracking-widest text-[0.8rem] font-semibold">Simulators</small>
        <p>Access a registry of biosimulation tools encompassing diverse frameworks, algorithms, and model format curated to streamline the simulation process. The registry, powered by conventions like SED-ML, offers readily available Python packages, command-line programs, and Docker images for consistent execution.</p>
        <div class="flex items-center gap-4 mt-3">
          <UButton class="w-max bg-transparent border-1 hover:bg-white/10 border-white text-white" variant="outline" color="neutral" icon="i-lucide-rocket" to="/simulators/get-started">Get Started</UButton>
          <UButton class="w-max bg-transparent border-1 hover:bg-white/10 border-white text-white" variant="outline" color="neutral" icon="i-lucide-search" to="/simulators">Browse Simulators</UButton>
        </div>
        <br>
        <small class="uppercase tracking-widest text-[0.8rem] font-semibold">Simulations</small>
        <p>Access a wide range of frameworks (e.g., logical, Flux-Balance Analysis (FBA), continuous kinetic, discrete kinetic), simulation algorithms (e.g., FBA, SSA), model formats (e.g., SBML), and tools (e.g., COBRApy, COPASI, tellurium) and connect with a community of others supercharging their bio-data processing.</p>
        <div class="flex items-center gap-4 mt-3">
          <UButton class="w-max bg-transparent border-1 hover:bg-white/10 border-white text-white" variant="outline" color="neutral" icon="i-lucide-sparkles" to="/simulations/run">Run a Simulation</UButton>
          <UButton class="w-max bg-transparent border-1 hover:bg-white/10 border-white text-white" variant="outline" color="neutral" icon="i-lucide-search" to="/simulations">Browse Simulation Runs</UButton>
        </div>
      </div>

      <div id="color_gradient" class="absolute top-0 left-0 w-full h-full z-2"></div>
      <div id="top_fade_gradient" class="absolute top-0 left-0 w-full h-full z-3"></div>
    </section>
    <section id="workflow" class="w-full px-6 text-center md:text-left">
      <div id="workflow_content" class="w-full max-w-[1200px] mx-auto flex flex-col gap-8">
        <div class="w-full flex flex-col gap-2">
          <h1 class="text-3xl font-bold">Your Workflow, Your Way</h1>
          <p class="text-lg">Starting from scratch? BioSimulations offers a nuts-to-bolts pipeline. Ready to import and upgrade some data? BioSimulations has extensive documentation to make supercharging your bio data easy.</p>
        </div>

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
              <div class="progress_wrapper w-full bg-gray-100 h-[6px] rounded-2xl relative overflow-hidden" v-if="workflow_feature.selected">
                <div class="progress_bar bg-blue-500 absolute w-0 h-full top-0 left-0 rounded-2xl group-hover:[--play-state:paused]" :style="{'--duration': `${workflow_feature.duration}ms`}" @animationend="switch_workflow_feature()"></div>
              </div>
            </div>
          </TransitionGroup>

          <!-- Static multimedia slot; renders the `associated_multimedia` of the current active WorkflowFeature -->
          <div id="multimedia_wrapper" class="w-full min-h-[100px] shadow-2xl overflow-hidden rounded-xl hidden md:block" data-aos="fade-up" data-aos-duration="1000">
            <img class="w-full h-full object-cover object-center block" v-if="current_workflow_feature && current_workflow_feature.associated_multimedia.type == 'image'" :src="current_workflow_feature.associated_multimedia.src" :alt="current_workflow_feature.associated_multimedia.alt">
          </div>
        </div>
      </div>
    </section>
    <section id="get_started" class="w-full bg-blue-500">
      <UPageCTA
        class=""
        variant="naked"
        title="Ready To Get Started?"
        description="The next step in your bio-simulations journey is here. The rest is up to you!"
        :links="cta_links"
        :ui="{
          wrapper: 'w-full flex flex-col items-center gap-[40px] py-[100px]',
          header: 'w-full flex flex-col items-center gap-[8px]',
          title: 'text-center text-white !text-3xl font-bold',
          container: 'max-w-[1200px] w-full px-4 !lg:px-0 !py-0 gap-y-[75px] mx-auto',
          description: 'mt-0 text-center text-white text-lg',
          links: 'flex flex-wrap gap-x-[16px] gap-y-[8px]',
          footer: 'mt-0'
        }"
      >
      </UPageCTA>
<!--      <div id="get_started_content" class="w-full max-w-[1200px] mx-auto flex flex-col gap-5 p-[100px]">
        <h1 class="text-center text-white text-3xl font-bold">Ready To Get Started?</h1>
        <p class="text-center text-white text-lg">The next step in your bio-simulations journey is here. The rest is up to you!</p>

        <div class="w-full flex items-center justify-center gap-4 mt-8">
          <UButton></UButton>
          <UButton></UButton>
          <UButton></UButton>
          <UButton></UButton>
        </div>
      </div>-->
    </section>
  </div>
</template>
<style>
  #bottom_fade_gradient {
    background: linear-gradient(to bottom, transparent 75%, white);
  }

  #revolutionary_platform {
    #color_gradient {
      background: linear-gradient(to bottom right, #2196f3 10%, #fe9700 100%);
    }

    #top_fade_gradient {
      background: linear-gradient(to top, transparent 75%, white);
    }
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
