import { defineConfig, type HeadConfig } from "vitepress";

const repo = "https://github.com/timothestoifl24/bulk-mailer";
const site = "https://bulkmailer.stoifl.app";
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

  lastUpdated: true,

  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: `${base}media/brand/logo.svg` }],
    ["meta", { name: "theme-color", content: "#4F46E5" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:site_name", content: "Bulk Mailer" }],
    ["meta", { property: "og:image", content: `${site}/media/brand/social-preview.png` }],
    ["meta", { property: "og:url", content: `${site}${base}` }],
    ["meta", { name: "twitter:card", content: "summary_large_image" }],
    ...devHead,
  ],

  sitemap: {
    hostname: `${site}${base}`,
  },

  themeConfig: {
    logo: "/media/brand/logo.svg",

    nav: [
      { text: "Guide", link: "/guide" },
      { text: "Screenshots", link: "/screenshots" },
      { text: "Setup", link: "/setup" },
      { text: "Kubernetes", link: "/kubernetes" },
      {
        text: "Reference",
        items: [
          { text: "Advanced config", link: "/advanced-config" },
          { text: "Upgrading", link: "/upgrading" },
          { text: "FAQ", link: "/faq" },
          { text: "Contributing", link: "/contributing" },
        ],
      },
      {
        text: "Releases",
        items: [
          { text: "Changelog", link: `${repo}/blob/${branch}/CHANGELOG.md` },
          { text: "All releases", link: `${repo}/releases` },
          { text: "Container image", link: "https://hub.docker.com/r/timothestoifl24/bulk-mailer" },
        ],
      },
    ],

    sidebar: [
      {
        text: "Getting started",
        items: [
          { text: "Overview", link: "/" },
          { text: "Setup", link: "/setup" },
          { text: "Kubernetes", link: "/kubernetes" },
          { text: "Guide", link: "/guide" },
          { text: "Screenshots", link: "/screenshots" },
        ],
      },
      {
        text: "Reference",
        items: [
          { text: "Advanced config", link: "/advanced-config" },
          { text: "Upgrading", link: "/upgrading" },
          { text: "FAQ", link: "/faq" },
        ],
      },
      {
        text: "Project",
        items: [
          { text: "Contributing", link: "/contributing" },
          { text: "Security policy", link: `${repo}/blob/main/SECURITY.md` },
          { text: "Changelog", link: `${repo}/blob/${branch}/CHANGELOG.md` },
        ],
      },
    ],

    socialLinks: [{ icon: "github", link: repo }],

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
