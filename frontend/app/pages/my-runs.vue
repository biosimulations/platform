  <script setup lang="ts">
  definePageMeta({
    middleware: 'auth'
  })
  </script>

  <template>
    <section class="p-6">
      <h1 class="text-2xl font-semibold">My simulation runs</h1>
      <p class="mt-2 text-muted">
        You are logged in. This page is ready to call the protected platform API.
      </p>
    </section>
  </template>
