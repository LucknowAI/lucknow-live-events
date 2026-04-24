const nextCoreWebVitals = require("eslint-config-next/core-web-vitals");

/** @type {import("eslint").Linter.FlatConfig[]} */
module.exports = [
  ...nextCoreWebVitals,
  {
    ignores: ["node_modules/**"],
  },
];

