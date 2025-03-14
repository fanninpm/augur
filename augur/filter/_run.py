from collections import defaultdict
import csv
import itertools
import json
import numpy as np
import os
import pandas as pd
from tempfile import NamedTemporaryFile

from augur.errors import AugurError
from augur.index import index_sequences, index_vcf
from augur.io.file import open_file
from augur.io.metadata import read_metadata
from augur.io.sequences import read_sequences, write_sequences
from augur.io.print import print_err
from augur.io.vcf import is_vcf as filename_is_vcf, write_vcf
from augur.types import EmptyOutputReportingMethod
from .io import cleanup_outputs, read_priority_scores
from .include_exclude_rules import apply_filters, construct_filters
from .subsample import PriorityQueue, TooManyGroupsError, calculate_sequences_per_group, create_queues_by_group, get_groups_for_subsampling


def run(args):
    # Determine whether the sequence index exists or whether should be
    # generated. We need to generate an index if the input sequences are in a
    # VCF, if sequence output has been requested (so we can filter strains by
    # sequences that are present), or if any other sequence-based filters have
    # been requested.
    sequence_strains = None
    sequence_index_path = args.sequence_index
    build_sequence_index = False
    is_vcf = filename_is_vcf(args.sequences)

    if sequence_index_path is None and args.sequences and not args.exclude_all:
        build_sequence_index = True

    if build_sequence_index:
        # Generate the sequence index on the fly, for backwards compatibility
        # with older workflows that don't generate the index ahead of time.
        # Create a temporary index using a random filename to avoid collisions
        # between multiple filter commands.
        with NamedTemporaryFile(delete=False) as sequence_index_file:
            sequence_index_path = sequence_index_file.name

        print_err(
            "Note: You did not provide a sequence index, so Augur will generate one.",
            "You can generate your own index ahead of time with `augur index` and pass it with `augur filter --sequence-index`."
        )

        if is_vcf:
            index_vcf(args.sequences, sequence_index_path)
        else:
            index_sequences(args.sequences, sequence_index_path)

    # Load the sequence index, if a path exists.
    sequence_index = None
    if sequence_index_path:
        sequence_index = pd.read_csv(
            sequence_index_path,
            sep="\t",
            index_col="strain",
        )

        # Remove temporary index file, if it exists.
        if build_sequence_index:
            os.unlink(sequence_index_path)

        # Calculate summary statistics needed for filtering.
        sequence_strains = set(sequence_index.index.values)

    #####################################
    #Filtering steps
    #####################################

    # Setup filters.
    exclude_by, include_by = construct_filters(
        args,
        sequence_index,
    )

    # Setup grouping. We handle the following major use cases:
    #
    # 1. group by and sequences per group defined -> use the given values by the
    # user to identify the highest priority records from each group in a single
    # pass through the metadata.
    #
    # 2. group by and maximum sequences defined -> use the first pass through
    # the metadata to count the number of records in each group, calculate the
    # sequences per group that satisfies the requested maximum, and use a second
    # pass through the metadata to select that many sequences per group.
    #
    # 3. group by not defined but maximum sequences defined -> use a "dummy"
    # group such that we select at most the requested maximum number of
    # sequences in a single pass through the metadata.
    #
    # Each case relies on a priority queue to track the highest priority records
    # per group. In the best case, we can track these records in a single pass
    # through the metadata. In the worst case, we don't know how many sequences
    # per group to use, so we need to calculate this number after the first pass
    # and use a second pass to add records to the queue.
    group_by = args.group_by
    sequences_per_group = args.sequences_per_group
    records_per_group = None

    if group_by and args.subsample_max_sequences:
        # In this case, we need two passes through the metadata with the first
        # pass used to count the number of records per group.
        records_per_group = defaultdict(int)
    elif not group_by and args.subsample_max_sequences:
        group_by = ("_dummy",)
        sequences_per_group = args.subsample_max_sequences

    # If we are grouping data, use queues to store the highest priority strains
    # for each group. When no priorities are provided, they will be randomly
    # generated.
    queues_by_group = None
    if group_by:
        # Use user-defined priorities, if possible. Otherwise, setup a
        # corresponding dictionary that returns a random float for each strain.
        if args.priority:
            priorities = read_priority_scores(args.priority)
        else:
            random_generator = np.random.default_rng(args.subsample_seed)
            priorities = defaultdict(random_generator.random)

    # Setup metadata output. We track whether any records have been written to
    # disk yet through the following variables, to control whether we write the
    # metadata's header and open a new file for writing.
    metadata_header = True
    metadata_mode = "w"

    # Setup strain output.
    if args.output_strains:
        output_strains = open(args.output_strains, "w")

    # Setup logging.
    output_log_writer = None
    if args.output_log:
        # Log the names of strains that were filtered or force-included, so we
        # can properly account for each strain (e.g., including those that were
        # initially filtered for one reason and then included again for another
        # reason).
        output_log = open(args.output_log, "w", newline='')
        output_log_header = ("strain", "filter", "kwargs")
        output_log_writer = csv.DictWriter(
            output_log,
            fieldnames=output_log_header,
            delimiter="\t",
            lineterminator="\n",
        )
        output_log_writer.writeheader()

    # Load metadata. Metadata are the source of truth for which sequences we
    # want to keep in filtered output.
    metadata_strains = set()
    valid_strains = set() # TODO: rename this more clearly
    all_sequences_to_include = set()
    filter_counts = defaultdict(int)

    metadata_reader = read_metadata(
        args.metadata,
        id_columns=args.metadata_id_columns,
        chunk_size=args.metadata_chunk_size,
    )
    for metadata in metadata_reader:
        duplicate_strains = (
            set(metadata.index[metadata.index.duplicated()]) |
            set(metadata.index[metadata.index.isin(metadata_strains)])
        )
        if len(duplicate_strains) > 0:
            cleanup_outputs(args)
            raise AugurError(f"The following strains are duplicated in '{args.metadata}':\n" + "\n".join(sorted(duplicate_strains)))

        # Maintain list of all strains seen.
        metadata_strains.update(set(metadata.index.values))

        # Filter metadata.
        seq_keep, sequences_to_filter, sequences_to_include = apply_filters(
            metadata,
            exclude_by,
            include_by,
        )
        valid_strains.update(seq_keep)

        # Track distinct strains to include, so we can write their
        # corresponding metadata, strains, or sequences later, as needed.
        distinct_force_included_strains = {
            record["strain"]
            for record in sequences_to_include
        }
        all_sequences_to_include.update(distinct_force_included_strains)

        # Track reasons for filtered or force-included strains, so we can
        # report total numbers filtered and included at the end. Optionally,
        # write out these reasons to a log file.
        for filtered_strain in itertools.chain(sequences_to_filter, sequences_to_include):
            filter_counts[(filtered_strain["filter"], filtered_strain["kwargs"])] += 1

            # Log the names of strains that were filtered or force-included,
            # so we can properly account for each strain (e.g., including
            # those that were initially filtered for one reason and then
            # included again for another reason).
            if args.output_log:
                output_log_writer.writerow(filtered_strain)

        if group_by:
            # Prevent force-included sequences from being included again during
            # subsampling.
            seq_keep = seq_keep - distinct_force_included_strains

            # If grouping, track the highest priority metadata records or
            # count the number of records per group. First, we need to get
            # the groups for the given records.
            group_by_strain, skipped_strains = get_groups_for_subsampling(
                seq_keep,
                metadata,
                group_by,
            )

            # Track strains skipped during grouping, so users know why those
            # strains were excluded from the analysis.
            for skipped_strain in skipped_strains:
                filter_counts[(skipped_strain["filter"], skipped_strain["kwargs"])] += 1
                valid_strains.remove(skipped_strain["strain"])

                if args.output_log:
                    output_log_writer.writerow(skipped_strain)

            if args.subsample_max_sequences and records_per_group is not None:
                # Count the number of records per group. We will use this
                # information to calculate the number of sequences per group
                # for the given maximum number of requested sequences.
                for group in group_by_strain.values():
                    records_per_group[group] += 1
            else:
                # Track the highest priority records, when we already
                # know the number of sequences allowed per group.
                if queues_by_group is None:
                    queues_by_group = {}

                for strain in sorted(group_by_strain.keys()):
                    # During this first pass, we do not know all possible
                    # groups will be, so we need to build each group's queue
                    # as we first encounter the group.
                    group = group_by_strain[strain]
                    if group not in queues_by_group:
                        queues_by_group[group] = PriorityQueue(
                            max_size=sequences_per_group,
                        )

                    queues_by_group[group].add(
                        metadata.loc[strain],
                        priorities[strain],
                    )

        # Always write out strains that are force-included. Additionally, if
        # we are not grouping, write out metadata and strains that passed
        # filters so far.
        force_included_strains_to_write = distinct_force_included_strains
        if not group_by:
            force_included_strains_to_write = force_included_strains_to_write | seq_keep

        if args.output_metadata:
            # TODO: wrap logic to write metadata into its own function
            metadata.loc[list(force_included_strains_to_write)].to_csv(
                args.output_metadata,
                sep="\t",
                header=metadata_header,
                mode=metadata_mode,
            )
            metadata_header = False
            metadata_mode = "a"

        if args.output_strains:
            # TODO: Output strains will no longer be ordered. This is a
            # small breaking change.
            for strain in force_included_strains_to_write:
                output_strains.write(f"{strain}\n")

    # In the worst case, we need to calculate sequences per group from the
    # requested maximum number of sequences and the number of sequences per
    # group. Then, we need to make a second pass through the metadata to find
    # the requested number of records.
    if args.subsample_max_sequences and records_per_group is not None:
        # Calculate sequences per group. If there are more groups than maximum
        # sequences requested, sequences per group will be a floating point
        # value and subsampling will be probabilistic.
        try:
            sequences_per_group, probabilistic_used = calculate_sequences_per_group(
                args.subsample_max_sequences,
                records_per_group.values(),
                args.probabilistic_sampling,
            )
        except TooManyGroupsError as error:
            raise AugurError(error)

        if (probabilistic_used):
            print(f"Sampling probabilistically at {sequences_per_group:0.4f} sequences per group, meaning it is possible to have more than the requested maximum of {args.subsample_max_sequences} sequences after filtering.")
        else:
            print(f"Sampling at {sequences_per_group} per group.")

        if queues_by_group is None:
            # We know all of the possible groups now from the first pass through
            # the metadata, so we can create queues for all groups at once.
            queues_by_group = create_queues_by_group(
                records_per_group.keys(),
                sequences_per_group,
                random_seed=args.subsample_seed,
            )

        # Make a second pass through the metadata, only considering records that
        # have passed filters.
        metadata_reader = read_metadata(
            args.metadata,
            id_columns=args.metadata_id_columns,
            chunk_size=args.metadata_chunk_size,
        )
        for metadata in metadata_reader:
            # Recalculate groups for subsampling as we loop through the
            # metadata a second time. TODO: We could store these in memory
            # during the first pass, but we want to minimize overall memory
            # usage at the moment.
            seq_keep = set(metadata.index.values) & valid_strains

            # Prevent force-included strains from being considered in this
            # second pass, as in the first pass.
            seq_keep = seq_keep - all_sequences_to_include

            group_by_strain, skipped_strains = get_groups_for_subsampling(
                seq_keep,
                metadata,
                group_by,
            )

            for strain in sorted(group_by_strain.keys()):
                group = group_by_strain[strain]
                queues_by_group[group].add(
                    metadata.loc[strain],
                    priorities[strain],
                )

    # If we have any records in queues, we have grouped results and need to
    # stream the highest priority records to the requested outputs.
    num_excluded_subsamp = 0
    if queues_by_group:
        # Populate the set of strains to keep from the records in queues.
        subsampled_strains = set()
        for group, queue in queues_by_group.items():
            records = []
            for record in queue.get_items():
                # Each record is a pandas.Series instance. Track the name of the
                # record, so we can output its sequences later.
                subsampled_strains.add(record.name)

                # Construct a data frame of records to simplify metadata output.
                records.append(record)

                if args.output_strains:
                    # TODO: Output strains will no longer be ordered. This is a
                    # small breaking change.
                    output_strains.write(f"{record.name}\n")

            # Write records to metadata output, if requested.
            if args.output_metadata and len(records) > 0:
                records = pd.DataFrame(records)
                records.to_csv(
                    args.output_metadata,
                    sep="\t",
                    header=metadata_header,
                    mode=metadata_mode,
                )
                metadata_header = False
                metadata_mode = "a"

        # Count and optionally log strains that were not included due to
        # subsampling.
        strains_filtered_by_subsampling = valid_strains - subsampled_strains
        num_excluded_subsamp = len(strains_filtered_by_subsampling)
        if output_log_writer:
            for strain in strains_filtered_by_subsampling:
                output_log_writer.writerow({
                    "strain": strain,
                    "filter": "subsampling",
                    "kwargs": "",
                })

        valid_strains = subsampled_strains

    # Force inclusion of specific strains after filtering and subsampling.
    valid_strains = valid_strains | all_sequences_to_include

    # Write output starting with sequences, if they've been requested. It is
    # possible for the input sequences and sequence index to be out of sync
    # (e.g., the index is a superset of the given sequences input), so we need
    # to update the set of strains to keep based on which strains are actually
    # available.
    if is_vcf:
        if args.output:
            # Get the samples to be deleted, not to keep, for VCF
            dropped_samps = list(sequence_strains - valid_strains)
            write_vcf(args.sequences, args.output, dropped_samps)
    elif args.sequences:
        sequences = read_sequences(args.sequences)

        # If the user requested sequence output, stream to disk all sequences
        # that passed all filters to avoid reading sequences into memory first.
        # Even if we aren't emitting sequences, we track the observed strain
        # names in the sequence file as part of the single pass to allow
        # comparison with the provided sequence index.
        if args.output:
            observed_sequence_strains = set()
            with open_file(args.output, "wt") as output_handle:
                for sequence in sequences:
                    observed_sequence_strains.add(sequence.id)

                    if sequence.id in valid_strains:
                        write_sequences(sequence, output_handle, 'fasta')
        else:
            observed_sequence_strains = {sequence.id for sequence in sequences}

        if sequence_strains != observed_sequence_strains:
            # Warn the user if the expected strains from the sequence index are
            # not a superset of the observed strains.
            if sequence_strains is not None and observed_sequence_strains > sequence_strains:
                print_err(
                    "WARNING: The sequence index is out of sync with the provided sequences.",
                    "Metadata and strain output may not match sequence output."
                )

            # Update the set of available sequence strains.
            sequence_strains = observed_sequence_strains

    # Calculate the number of strains that don't exist in either metadata or
    # sequences.
    num_excluded_by_lack_of_metadata = 0
    if sequence_strains:
        # Update strains to keep based on available sequence data. This prevents
        # writing out strain lists or metadata for strains that have no
        # sequences.
        valid_strains = valid_strains & sequence_strains

        num_excluded_by_lack_of_metadata = len(sequence_strains - metadata_strains)

    if args.output_strains:
        output_strains.close()

    # Calculate the number of strains passed and filtered.
    total_strains_passed = len(valid_strains)
    total_strains_filtered = len(metadata_strains) + num_excluded_by_lack_of_metadata - total_strains_passed

    print(f"{total_strains_filtered} strains were dropped during filtering")

    if num_excluded_by_lack_of_metadata:
        print(f"\t{num_excluded_by_lack_of_metadata} had no metadata")

    report_template_by_filter_name = {
        "filter_by_sequence_index": "{count} had no sequence data",
        "filter_by_exclude_all": "{count} of these were dropped by `--exclude-all`",
        "filter_by_exclude": "{count} of these were dropped because they were in {exclude_file}",
        "filter_by_exclude_where": "{count} of these were dropped because of '{exclude_where}'",
        "filter_by_query": "{count} of these were filtered out by the query: \"{query}\"",
        "filter_by_ambiguous_date": "{count} of these were dropped because of their ambiguous date in {ambiguity}",
        "filter_by_min_date": "{count} of these were dropped because they were earlier than {min_date} or missing a date",
        "filter_by_max_date": "{count} of these were dropped because they were later than {max_date} or missing a date",
        "filter_by_sequence_length": "{count} of these were dropped because they were shorter than minimum length of {min_length}bp",
        "filter_by_non_nucleotide": "{count} of these were dropped because they had non-nucleotide characters",
        "skip_group_by_with_ambiguous_year": "{count} were dropped during grouping due to ambiguous year information",
        "skip_group_by_with_ambiguous_month": "{count} were dropped during grouping due to ambiguous month information",
        "skip_group_by_with_ambiguous_day": "{count} were dropped during grouping due to ambiguous day information",
        "force_include_strains": "{count} strains were added back because they were in {include_file}",
        "force_include_where": "{count} sequences were added back because of '{include_where}'",
    }
    for (filter_name, filter_kwargs), count in filter_counts.items():
        if filter_kwargs:
            parameters = dict(json.loads(filter_kwargs))
        else:
            parameters = {}

        parameters["count"] = count
        print("\t" + report_template_by_filter_name[filter_name].format(**parameters))

    if (group_by and args.sequences_per_group) or args.subsample_max_sequences:
        seed_txt = ", using seed {}".format(args.subsample_seed) if args.subsample_seed else ""
        print("\t%i of these were dropped because of subsampling criteria%s" % (num_excluded_subsamp, seed_txt))

    if total_strains_passed == 0:
        empty_results_message = "All samples have been dropped! Check filter rules and metadata file format."
        if args.empty_output_reporting is EmptyOutputReportingMethod.ERROR:
            raise AugurError(empty_results_message)
        elif args.empty_output_reporting is EmptyOutputReportingMethod.WARN:
            print_err(f"WARNING: {empty_results_message}")
        elif args.empty_output_reporting is EmptyOutputReportingMethod.SILENT:
            pass
        else:
            raise ValueError(f"Encountered unhandled --empty-output-reporting method {args.empty_output_reporting!r}")

    print(f"{total_strains_passed} strains passed all filters")
