<script setup lang="ts">
import { useData, useRoute } from 'vitepress'

const { site } = useData()
const route = useRoute()
</script>

<template>
  <div v-if="site.base === '/dev/'" class="dev-banner">
    <span class="message">
      <span class="long">Development docs for the next release, built from <code>develop</code>.</span>
      <span class="short">Development docs</span>
    </span>
    <a :href="'/' + route.path.slice(site.base.length)" target="_self">Switch to production</a>
  </div>
</template>

<style scoped>
/* The height matches --vp-layout-top-height, which config.mts sets for development builds. */
.dev-banner {
  position: fixed;
  top: 0;
  z-index: var(--vp-z-index-layout-top);
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 40px;
  padding: 0 16px;
  /* The soft tint is translucent; layer it over the page colour so scrolled content doesn't show through. */
  background: linear-gradient(var(--vp-c-warning-soft), var(--vp-c-warning-soft)), var(--vp-c-bg);
  color: var(--vp-c-text-1);
  font-size: 13px;
  white-space: nowrap;
}
/* Only the message gives way on narrow screens; the link always stays whole. */
.message {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dev-banner a {
  flex-shrink: 0;
  color: var(--vp-c-brand-1);
  font-weight: 500;
}
.short {
  display: none;
}
@media (max-width: 640px) {
  .long {
    display: none;
  }
  .short {
    display: inline;
  }
}
</style>
