import csv
import os
import pandas as pd
import pyfastx
import sys
from io import StringIO
from itertools import chain

from augur.errors import AugurError
from augur.io.print import print_err
from augur.types import DataErrorMethod
from .file import open_file


def read_metadata(metadata_file, id_columns=("strain", "name"), chunk_size=None):
    """Read metadata from a given filename and into a pandas `DataFrame` or
    `TextFileReader` object.

    Parameters
    ----------
    metadata_file : str
        Path to a metadata file to load.
    id_columns : list of str
        List of possible id column names to check for, ordered by priority.
    chunk_size : int
        Size of chunks to stream from disk with an iterator instead of loading the entire input file into memory.

    Returns
    -------
    pandas.DataFrame or `pandas.io.parsers.TextFileReader`

    Raises
    ------
    KeyError
        When the metadata file does not have any valid index columns.

    Examples
    --------

    For standard use, request a metadata file and get a pandas DataFrame.

    >>> read_metadata("tests/functional/filter/data/metadata.tsv").index.values[0]
    'COL/FLR_00024/2015'

    Requesting an index column that doesn't exist should produce an error.

    >>> read_metadata("tests/functional/filter/data/metadata.tsv", id_columns=("Virus name",))
    Traceback (most recent call last):
      ...
    Exception: None of the possible id columns (('Virus name',)) were found in the metadata's columns ('strain', 'virus', 'accession', 'date', 'region', 'country', 'division', 'city', 'db', 'segment', 'authors', 'url', 'title', 'journal', 'paper_url')

    We also allow iterating through metadata in fixed chunk sizes.

    >>> for chunk in read_metadata("tests/functional/filter/data/metadata.tsv", chunk_size=5):
    ...     print(chunk.shape)
    ...
    (5, 14)
    (5, 14)
    (2, 14)

    """
    kwargs = {
        "sep": None,
        "engine": "python",
        "skipinitialspace": True,
        "na_filter": False,
    }

    if chunk_size:
        kwargs["chunksize"] = chunk_size

    # Inspect the first chunk of the metadata, to find any valid index columns.
    metadata = pd.read_csv(
        metadata_file,
        iterator=True,
        **kwargs,
    )
    chunk = metadata.read(nrows=1)
    metadata.close()

    id_columns_present = [
        id_column
        for id_column in id_columns
        if id_column in chunk.columns
    ]

    # If we couldn't find a valid index column in the metadata, alert the user.
    if not id_columns_present:
        raise Exception(f"None of the possible id columns ({id_columns!r}) were found in the metadata's columns {tuple(chunk.columns)!r}")
    else:
        index_col = id_columns_present[0]

    # If we found a valid column to index the DataFrame, specify that column and
    # also tell pandas that the column should be treated like a string instead
    # of having its type inferred. This latter argument allows users to provide
    # numerical ids that don't get converted to numbers by pandas.
    kwargs["index_col"] = index_col
    kwargs["dtype"] = {index_col: "string"}

    return pd.read_csv(
        metadata_file,
        **kwargs
    )


def read_table_to_dict(table, duplicate_reporting=DataErrorMethod.ERROR_FIRST, id_column=None):
    """
    Read rows from *table* file and yield each row as a single dict.

    Will report duplicate records based on the *id_column* if requested via
    *duplicate_reporting* after the generator has been exhausted.

    Parameters
    ----------
    table: str
        Path to a CSV or TSV file or IO buffer

    duplicate_reporting: DataErrorMethod, optional
        How should duplicate records be reported

    id_column: str, optional
        Name of the column that contains the record identifier used for reporting duplicates.
        Uses the first column of the metadata if not provided.

    Yields
    ------
    dict:
        The parsed row as a single record

    Raises
    ------
    AugurError
        Raised for any of the following reasons:
        1. There are parsing errors from the csv standard library
        2. The provided *id_column* does not exist in the *metadata*
        3. The *duplicate_reporting* method is set to ERROR_FIRST or ERROR_ALL and duplicate(s) are found
    """
    valid_delimiters = [',', '\t']
    seen_ids = set()
    duplicate_ids = set()
    with open_file(table) as handle:
        # Get sample to determine delimiter
        table_sample = handle.readline()

        if handle.seekable():
            handle.seek(0)
        else:
            table_sample_file = StringIO(table_sample)
            handle = chain(table_sample_file, handle)

        try:
            dialect = csv.Sniffer().sniff(table_sample, valid_delimiters)
        except csv.Error as err:
            raise AugurError(
                f"Could not determine the delimiter of {table!r}. "
                "File must be a CSV or TSV."
            ) from err

        metadata_reader = csv.DictReader(handle, dialect=dialect)
        if duplicate_reporting is DataErrorMethod.SILENT:
            # Directly yield from metadata reader since we do not need to check for duplicate ids
            yield from metadata_reader
        else:
            if id_column is None:
                id_column = metadata_reader.fieldnames[0]

            for record in metadata_reader:
                record_id = record.get(id_column)
                if record_id is None:
                    raise AugurError(f"The provided id column {id_column!r} does not exist in {table!r}.")

                if record_id in seen_ids:
                    # Immediately raise an error if requested to error on the first duplicate
                    if duplicate_reporting is DataErrorMethod.ERROR_FIRST:
                        raise AugurError(f"Encountered record with duplicate id {record_id!r} in {table!r}")

                    # Give immediate feedback on duplicates if requested to warn on duplicates
                    # We'll also print a full summary of duplicates once the generator is exhausted
                    if duplicate_reporting is DataErrorMethod.WARN:
                        print_err(f"WARNING: Encountered record with duplicate id {record_id!r} in {table!r}")

                    duplicate_ids.add(record_id)
                else:
                    seen_ids.add(record_id)

                yield record

    if duplicate_reporting is not DataErrorMethod.SILENT and duplicate_ids:
        duplicates_message = f"The following records are duplicated in {table!r}:\n" + "\n".join(map(repr, sorted(duplicate_ids)))

        if duplicate_reporting is DataErrorMethod.WARN:
            print_err(f"WARNING: {duplicates_message}")
        elif duplicate_reporting is DataErrorMethod.ERROR_ALL:
            raise AugurError(duplicates_message)
        else:
            raise ValueError(f"Encountered unhandled duplicate reporting method: {duplicate_reporting!r}")


def read_metadata_with_sequences(metadata, fasta, seq_id_column, seq_field='sequence',
    unmatched_reporting=DataErrorMethod.ERROR_FIRST, duplicate_reporting=DataErrorMethod.ERROR_FIRST):
    """
    Read rows from *metadata* file and yield each row as a single dict that has
    been updated with their corresponding sequence from the *fasta* file.
    Matches the metadata record with sequences using the sequence id provided
    in the *seq_id_column*. To ensure that the sequences can be matched with
    the metadata, the FASTA headers must contain the matching sequence id. The
    FASTA headers may include additional description parts after the id, but
    they will not be used to match the metadata.

    Will report unmatched records if requested via *unmatched_reporting*.
    Note the ERROR_FIRST method will raise an error at the first unmatched metadata record
    but not for an unmatched sequence record because we can only check for unmatched sequences
    after exhausting the metadata generator.

    Will report duplicate records if requested via *duplicate_reporting*.

    Reads the *fasta* file with `pyfastx.Fasta`, which creates an index for
    the file to allow random access of sequences via the sequence id.
    Will remove any existing index file named `<fasta>.fxi` to force the
    rebuilding of the index so that there's no chance of using stale cached indexes.
    See pyfastx docs for more details:
    https://pyfastx.readthedocs.io/en/latest/usage.html#fasta

    Parameters
    ----------
    metadata: str
        Path to a CSV or TSV metadata file

    fasta: str
        Path to a plain or gzipped FASTA file

    seq_id_column: str
        The column in the metadata file that contains the sequence id for
        matching sequences

    seq_field: str, optional
        The field name to use for the sequence in the updated record

    unmatched_reporting: DataErrorMethod, optional
        How should unmatched records be reported

    duplicate_reporting: DataErrorMethod, optional
        How should duplicate records be reported

    Yields
    ------
    dict
        The parsed metadata record with the sequence
    """
    # Remove the old Pyfastx index to force rebuild of index
    # so we don't have to worry about a stale cached index
    try:
        os.remove(f"{fasta}.fxi")
    except FileNotFoundError:
        pass

    sequences = pyfastx.Fasta(fasta)
    sequence_ids = set(sequences.keys())

    # Used for determining unmatched records
    processed_sequence_ids = set()
    unmatched_metadata_ids = set()

    # Used for determining duplicate records
    processed_metadata_ids = set()
    duplicate_metadata_ids = set()
    duplicate_sequence_ids = set()

    # First check for duplicates in FASTA first since pyfastx will only return
    # the first sequence of duplicates, which may lead to unexpected results.
    # Look for duplicate sequence ids if the number of sequences does not match the number of unique ids
    if duplicate_reporting is not DataErrorMethod.SILENT and len(sequences) != len(sequence_ids):
        seen_sequence_ids = set()
        for seq_id in sequences.keys():
            if seq_id in seen_sequence_ids:
                # Immediately raise an error if requested to error on the first duplicate
                if duplicate_reporting is DataErrorMethod.ERROR_FIRST:
                    raise AugurError(f"Encountered sequence record with duplicate id {seq_id!r}.")

                # Give immediate feedback on duplicates if requested to warn on duplicates
                # We'll also print a full summary of duplicates at the end of the command
                if duplicate_reporting is DataErrorMethod.WARN:
                    print_err(f"WARNING: Encountered sequence record with duplicate id {seq_id!r}.")

                duplicate_sequence_ids.add(seq_id)
            else:
                seen_sequence_ids.add(seq_id)

    # Silencing duplicate reporting here because we will need to handle duplicates
    # in both the metadata and FASTA files after processing all the records here.
    for record in read_table_to_dict(metadata, duplicate_reporting=DataErrorMethod.SILENT):
        seq_id = record.get(seq_id_column)

        if seq_id is None:
            raise AugurError(f"The provided sequence id column {seq_id_column!r} does not exist in the metadata.")

        # Keep track of duplicate ids to report duplicate records if requested
        if seq_id in processed_metadata_ids:
            # Immediately raise an error if requested to error on the first duplicate
            if duplicate_reporting is DataErrorMethod.ERROR_FIRST:
                raise AugurError(f"Encountered metadata record with duplicate id {seq_id!r}.")

            # Give immediate feedback on duplicates if requested to warn on duplicates
            # We'll also print a full summary of duplicates at the end of the command
            if duplicate_reporting is DataErrorMethod.WARN:
                print_err(f"WARNING: Encountered metadata record with duplicate id {seq_id!r}.")

            duplicate_metadata_ids.add(seq_id)
        else:
            processed_metadata_ids.add(seq_id)

        # Skip records that do not have a matching sequence
        # TODO: change this to try/except to fetch sequences and catch
        # KeyError for non-existing sequences when https://github.com/lmdu/pyfastx/issues/50 is resolved
        if seq_id not in sequence_ids:
            # Immediately raise an error if requested to error on the first unmatched record
            if unmatched_reporting is DataErrorMethod.ERROR_FIRST:
                raise AugurError(f"Encountered metadata record {seq_id!r} without a matching sequence.")

            # Give immediate feedback on unmatched records if requested to warn on unmatched
            # We'll also print a full summary of unmatched records at the end of the command
            if unmatched_reporting is DataErrorMethod.WARN:
                print_err(f"WARNING: Encountered metadata record {seq_id!r} without a matching sequence.")

            # Save unmatched metadata ids to report unmatched records if requested
            unmatched_metadata_ids.add(seq_id)
            continue

        sequence_record = sequences[seq_id]
        record[seq_field] = str(sequence_record.seq).upper()
        # Save processed sequence ids to be able to determine if sequences were unmatched
        processed_sequence_ids.add(seq_id)

        yield record

    # Create summary of duplicate records if requested
    duplicates_message = None
    if duplicate_reporting is not DataErrorMethod.SILENT and (duplicate_metadata_ids or duplicate_sequence_ids):
        duplicates_message = "The output may not match expectations because there were records with duplicate sequence ids."

        if duplicate_metadata_ids:
            duplicates_message += f"\nThe following sequence ids were duplicated in {metadata!r}:\n"
            duplicates_message += "\n".join(map(repr, sorted(duplicate_metadata_ids)))

        if duplicate_sequence_ids:
            duplicates_message += f"\nThe following sequence ids were duplicated in {fasta!r}:\n"
            duplicates_message += "\n".join(map(repr, sorted(duplicate_sequence_ids)))

    # Create summary for unmatched records if requested
    # Note this is where we find unmatched sequences because we can only do so after looping through all of the metadata
    unmatched_message = None
    unmatched_sequence_ids = sequence_ids - processed_sequence_ids
    if unmatched_reporting is not DataErrorMethod.SILENT and (unmatched_metadata_ids or unmatched_sequence_ids):
        unmatched_message = "The output may be incomplete because there were unmatched records."

        if unmatched_metadata_ids:
            unmatched_message += "\nThe following metadata records did not have a matching sequence:\n"
            unmatched_message += "\n".join(map(repr, sorted(unmatched_metadata_ids)))

        if unmatched_sequence_ids:
            unmatched_message += "\nThe following sequence records did not have a matching metadata record:\n"
            unmatched_message += "\n".join(map(repr, sorted(unmatched_sequence_ids)))


    # Handle all the different combinations for warnings and errors for unmatched and duplicate records
    # Make sure we output warnings before raising any errors
    if duplicate_reporting is DataErrorMethod.WARN and duplicates_message is not None:
        print_err(f"WARNING: {duplicates_message}")

    if unmatched_reporting is DataErrorMethod.WARN and unmatched_message is not None:
        print_err(f"WARNING: {unmatched_message}")

    # Combine error messages so both messages can be included in the final error
    error_message = ""
    if duplicate_reporting is DataErrorMethod.ERROR_ALL and duplicates_message is not None:
        error_message += "\n" + duplicates_message

    # We need to check ERROR_FIRST here for unmatched sequences since we
    # need to process all metadata records to know which sequences are unmatched
    if unmatched_reporting in {DataErrorMethod.ERROR_FIRST, DataErrorMethod.ERROR_ALL} and unmatched_message is not None:
        error_message += "\n" + unmatched_message

    if error_message:
        raise AugurError(f"Encountered the following error(s) when parsing metadata with sequences:{error_message}")


def write_records_to_tsv(records, output_file):
    """
    Write each record from *records* as a single row to a TSV *output_file*.
    Uses the keys of the first record as output column names.
    Ignores extra keys in other records.
    If records are missing keys, they will have an empty string as the value.

    Parameters
    ----------
    records: iterable of dict
        Iterator that yields dict that contains sequences

    output_file: str
        Path to the output TSV file.
        Accepts '-' to output TSV to stdout.
    """
    # Use the keys of the first record as output fields
    try:
        first_record = next(records)
    except StopIteration:
        raise AugurError(f"Unable to write records to {output_file} because provided records were empty.")

    # Use the record keys as output columns since as of python 3.7 dicts retain insertion order
    output_columns = list(first_record.keys())

    # Special case single hyphen as stdout
    if output_file == '-':
        output_file = sys.stdout

    with open_file(output_file, 'w', newline='') as output_metadata:
        tsv_writer = csv.DictWriter(
            output_metadata,
            output_columns,
            extrasaction='ignore',
            delimiter='\t',
            lineterminator='\n'
        )
        tsv_writer.writeheader()
        tsv_writer.writerow(first_record)

        for record in records:
            tsv_writer.writerow(record)
