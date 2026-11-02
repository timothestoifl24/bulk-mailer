import { defineConfig, type HeadConfig } from "vitepress";

const repo = "https://github.com/timothestoifl24/bulk-mailer";
const site = "https://bulkmailer.timothestoifl24.dev";
// docs.yml builds main at / (production) and develop at /dev/ (development) into one site.
const dev = process.env.DOCS_CHANNEL === "development";
const base = dev ? "/dev/" : "/";
const branch = dev ? "develop" : "main";
// Search engines should only list the production docs; the banner needs room above the nav.
const devHead: HeadConfig[] = dev
  ? [
      ["meta", { name: "robots", content: "noindex" }],
      ["style", {}, ":root { --vp-layout-top-height: 40px; }"],
    ]
  : [];

export default defineConfig({
  title: "Bulk Mailer",
  description:
    "Send one message to many recipients - typed in, pasted, uploaded as CSV, or pulled straight from LDAP / Active Directory. Self-hosted, no CDN, no JavaScript build step.",
  lang: "en-GB",
  base,

  // The requested URL shape - /guide, /setup, /faq - rather than /guide.html.
  // GitHub Pages resolves an extensionless request to the matching .html, so
  // this needs no server configuration on that host.
  cleanUrls: true,

  // A dead internal link should fail the build in CI, not ship.
  ignoreDeadLinks: false,

  // public/ is copied as is; the Markdown that mirror.mjs brings in there
  // (assets/icons/README.md) is not a page of the site.
  srcExclude: ["public/**"],

  lastUpdated: true,

  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: `${base}media/brand/logo.svg` }],
    ["meta", { name: "theme-color", content: "#4F46E5" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:site_name", content: "Bulk Mailer" }],
    ["meta", { property: "og:image", content: `${site}/media/brand/social-preview.png` }],
    ["meta", { property: "og:url", content: `${site}${base}` }],
    ["meta", { name: "twitter:card", content: "summary_large_image" }],
    // Ko-fi "Support me" button, in the brand indigo. It draws into <body>, so wait for the page to load;
    // offline or with Ko-fi blocked the script never arrives, and the button is simply left out.
    ["script", { src: "https://storage.ko-fi.com/cdn/scripts/overlay-widget.js", defer: "" }],
    [
      "script",
      {},
      `window.addEventListener("load", () => typeof kofiWidgetOverlay !== "undefined" && kofiWidgetOverlay.draw("timothestoifl24", {
        "type": "floating-chat",
        "floating-chat.donateButton.text": "Support me",
        "floating-chat.donateButton.background-color": "#4F46E5",
        "floating-chat.donateButton.text-color": "#fff"
      }))`,
    ],
    ...devHead,
  ],

  sitemap: {
    hostname: `${site}${base}`,
    // kubernetes.md only forwards the old URL to setup/kubernetes.
    transformItems: (items) => items.filter((item) => item.url !== "kubernetes"),
  },

  themeConfig: {
    logo: "/media/brand/logo.svg",

    nav: [
      { text: "Guide", link: "/guide" },
      { text: "Screenshots", link: "/screenshots" },
      {
        text: "Setup",
        activeMatch: "^/(setup/|advanced-config|upgrading)",
        items: [
          {
            text: "Deploy",
            items: [
              { text: "Docker/Podman", link: "/setup/docker" },
              { text: "Kubernetes", link: "/setup/kubernetes" },
              { text: "From source", link: "/setup/from-source" },
            ],
          },
          {
            // No heading: these follow the Deploy group under a divider.
            items: [
              { text: "Advanced config", link: "/advanced-config" },
              { text: "Upgrading", link: "/upgrading" },
            ],
          },
        ],
      },
      {
        text: "Reference",
        activeMatch: "^/(contributing|security|changelog|releases)",
        items: [
          { text: "Contributing", link: "/contributing" },
          { text: "Security policy", link: "/security" },
          { text: "Changelog", link: "/changelog" },
          { text: "Releases", link: "/releases" },
        ],
      },
      { text: "FAQ", link: "/faq" },
    ],

    sidebar: [
      {
        text: "Getting started",
        items: [
          { text: "Overview", link: "/" },
          { text: "Guide", link: "/guide" },
          { text: "Screenshots", link: "/screenshots" },
        ],
      },
      {
        text: "Setup",
        link: "/setup/",
        items: [
          {
            text: "Deploy",
            collapsed: false,
            items: [
              { text: "Docker/Podman", link: "/setup/docker" },
              { text: "Kubernetes", link: "/setup/kubernetes" },
              { text: "From source", link: "/setup/from-source" },
            ],
          },
          { text: "Advanced config", link: "/advanced-config" },
          { text: "Upgrading", link: "/upgrading" },
        ],
      },
      {
        text: "Reference",
        items: [
          { text: "Contributing", link: "/contributing" },
          { text: "Security policy", link: "/security" },
          { text: "Changelog", link: "/changelog" },
          { text: "Releases", link: "/releases" },
        ],
      },
      { text: "FAQ", link: "/faq" },
    ],

    // Rendered after the light/dark switch, in this order: Docker Hub, then GitHub.
    socialLinks: [
      {
        icon: "docker",
        link: "https://hub.docker.com/r/timothestoifl24/bulk-mailer",
        ariaLabel: "Bulk Mailer on Docker Hub",
      },
      { icon: "github", link: repo },
    ],

    editLink: {
      pattern: `${repo}/edit/${branch}/docs/:path`,
      text: "Edit this page on GitHub",
    },

    // Local search rather than Algolia: it is built into the bundle, so the
    // site keeps working offline and no visitor's query leaves the origin.
    search: {
      provider: "local",
    },

    footer: {
      message: `Released under the <a href="${repo}/blob/main/LICENSE">MIT licence</a>.`,
      copyright: "Copyright © Timothé Stoifl",
    },

    outline: [2, 3],

    docFooter: {
      prev: "Previous",
      next: "Next",
    },
  },
});
