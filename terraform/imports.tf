# Environment-specific import blocks live in wib-boss-finder/imports.tf.
# For wib-boss-finder, symlink that file into the root before running terraform:
#
#   ln -sf wib-boss-finder/imports.tf imports-env.tf
#
# imports-env.tf is gitignored so it has no effect on other environments.
# For fresh environments (dev, prod) no action needed — resources are created from scratch.
