const [major, minor] = process.versions.node.split('.').map(Number);

if (major !== 24 || minor < 18) {
  console.error(
    `KLM frontend requires Node >=24.18.0 <25; current runtime is ${process.version}. `
      + 'Use the version declared in .nvmrc.',
  );
  process.exit(1);
}
