import { defineConfig } from "vitepress";

const repo = "https://github.com/timothestoifl24/bulk-mailer";

export default defineConfig({
  title: "Bulk Mailer",
  description:
    "Send one message to many recipients - typed in, pasted, uploaded as CSV, or pulled straight from LDAP / Active Directory. Self-hosted, no CDN, no JavaScript build step.",
  lang: "en-GB",

  // The requested URL shape - /guide, /setup, /faq - rather than /guide.html.
  // GitHub Pages resolves an extensionless request to the matching .html, so
  // this needs no server configuration on that host.
  cleanUrls: true,

  // A dead internal link should fail the build in CI, not ship.
  ignoreDeadLinks: false,

  lastUpdated: true,

  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: "/media/brand/logo.svg" }],
    ["meta", { name: "theme-color", content: "#206bc4" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:site_name", content: "Bulk Mailer" }],
    ["meta", { property: "og:image", content: "https://bulkmailer.stoifl.app/media/brand/social-preview.png" }],
    ["meta", { property: "og:url", content: "https://bulkmailer.stoifl.app/" }],
    ["meta", { name: "twitter:card", content: "summary_large_image" }],
  ],

  sitemap: {
    hostname: "https://bulkmailer.stoifl.app/",
  },

  themeConfig: {
    logo: "/media/brand/logo.svg",

    nav: [
      { text: "Guide", link: "/guide" },
      { text: "Screenshots", link: "/screenshots" },
      { text: "Setup", link: "/setup" },
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
          { text: "Changelog", link: `${repo}/blob/main/CHANGELOG.md` },
          { text: "All releases", link: `${repo}/releases` },
          { text: "Container image", link: `${repo}/pkgs/container/bulk-mailer` },
        ],
      },
    ],

    sidebar: [
      {
        text: "Getting started",
        items: [
          { text: "Overview", link: "/" },
          { text: "Setup", link: "/setup" },
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
          { text: "Changelog", link: `${repo}/blob/main/CHANGELOG.md` },
        ],
      },
    ],

    socialLinks: [{ icon: "github", link: repo }],

    editLink: {
      pattern: `${repo}/edit/main/docs/:path`,
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
