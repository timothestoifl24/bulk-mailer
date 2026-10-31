<script setup lang="ts">
import { computed } from 'vue'
import { useData, useRoute } from 'vitepress'

// Production (main) is served at /, development (develop) at /dev/; see docs.yml.
const channels = [
  { name: 'Production', base: '/' },
  { name: 'Development', base: '/dev/' },
]

const { site } = useData()
const route = useRoute()

// The same page on the other channel. A page that only exists on develop shows the 404 page.
const page = computed(() => route.path.slice(site.value.base.length))
</script>

<template>
  <div class="channel-switch" role="group" aria-label="Documentation version">
    <!-- target="_self": a full page load, as the other channel is a separate site -->
    <a
      v-for="channel in channels"
      :key="channel.base"
      :href="channel.base + page"
      target="_self"
      :class="{ active: channel.base === site.base }"
      :aria-current="channel.base === site.base ? 'page' : undefined"
    >{{ channel.name }}</a>
  </div>
</template>

<style scoped>
.channel-switch {
  display: inline-flex;
  margin-left: 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  padding: 2px;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}
.channel-switch a {
  padding: 2px 10px;
  border-radius: 6px;
  color: var(--vp-c-text-2);
  text-decoration: none;
  transition: color 0.2s, background-color 0.2s;
}
.channel-switch a:hover {
  color: var(--vp-c-text-1);
}
.channel-switch a.active {
  background-color: var(--vp-c-default-soft);
  color: var(--vp-c-text-1);
}
</style>
