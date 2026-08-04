

<!--
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
-->

<script setup lang="ts">
const { loginWithRedirect } = useAuth0()
const config = useRuntimeConfig()

const showForgotPassword = ref(false)
const forgotEmail = ref('')
const forgotStatus = ref<'idle' | 'sending' | 'sent'>('idle')

function login() {
  loginWithRedirect()
}
function signup() {
  loginWithRedirect({ authorizationParams: { screen_hint: 'signup' } })
}

async function submitForgotPassword() {
  forgotStatus.value = 'sending'
  try {
    await $fetch(`https://${config.public.auth0Domain}/dbconnections/change_password`, {
      method: 'POST',
      body: {
        client_id: config.public.auth0ClientId,
        email: forgotEmail.value,
        connection: 'Username-Password-Authentication',
      },
    })
  } finally {
    // Always show the same generic message — don't reveal whether the email exists
    forgotStatus.value = 'sent'
  }
}
</script>

<template>
  <div class="auth-buttons">
    <button @click="login">Log In</button>
    <button @click="signup">Sign Up</button>
    <button class="link" @click="showForgotPassword = true">Forgot password?</button>

    <div v-if="showForgotPassword" class="forgot-password-modal">
      <template v-if="forgotStatus !== 'sent'">
        <p>Enter your email and we'll send you a reset link.</p>
        <input v-model="forgotEmail" type="email" placeholder="you@example.com" />
        <button :disabled="forgotStatus === 'sending'" @click="submitForgotPassword">
          Send reset link
        </button>
      </template>
      <p v-else>If that email is registered, a reset link is on its way.</p>
      <button @click="showForgotPassword = false">Close</button>
    </div>
  </div>
</template>
