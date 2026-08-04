<script setup lang="ts">
const { loginWithRedirect, logout, user, isAuthenticated } = useAuth0()
</script>

<template>
  <button v-if="!isAuthenticated" @click="loginWithRedirect()">
    Log in / Sign up
  </button>
  <div v-else>
    {{ user?.email }}
    <button @click="logout({ logoutParams: { returnTo: window.location.origin } })">
      Log out
    </button>
  </div>
</template>
