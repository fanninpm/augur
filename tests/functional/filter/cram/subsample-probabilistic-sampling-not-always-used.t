Setup

  $ source "$TESTDIR"/_setup.sh

Ensure probabilistic sampling is not used when unnecessary.

  $ ${AUGUR} filter \
  >  --metadata "$TESTDIR/../data/metadata.tsv" \
  >  --group-by region year month \
  >  --subsample-max-sequences 10 \
  >  --probabilistic-sampling \
  >  --subsample-seed 314159 \
  >  --output-metadata filtered_metadata.tsv
  Sampling at 10 per group.
  2 strains were dropped during filtering
  \t1 were dropped during grouping due to ambiguous year information (esc)
  \t1 were dropped during grouping due to ambiguous month information (esc)
  \t0 of these were dropped because of subsampling criteria, using seed 314159 (esc)
  10 strains passed all filters
