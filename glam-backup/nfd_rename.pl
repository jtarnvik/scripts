#!/usr/bin/perl
# =============================================================================
# nfd_rename.pl
#
# Renames every file and folder under a folder whose name is not in Unicode NFD
# form (decomposed: "ö" as "o" + combining diaeresis) to its NFD form.
#
# Why: the source (APFS) keeps names exactly as written, usually NFC; the backup
# target (HFS+) always stores them as NFD. rsync then sees the backup copy of an
# NFC-named file as a different file, deletes it and copies it again on every
# run — and with --backup-dir, a fresh copy lands among the deleted files every
# week. With every name in NFD on both sides the names match byte for byte.
# APFS treats the two forms as the same name, so the rename is safe in place.
#
# Usage: nfd_rename.pl [--dry-run] <folder>
# Prints one "renamed: <path>" line per rename ("would rename:" with --dry-run).
# Exits 1 if a rename fails.
# =============================================================================
use strict;
use warnings;
use File::Find;
use Unicode::Normalize qw(NFD);
use Encode qw(decode encode);

my $dry_run = 0;
if (@ARGV && $ARGV[0] eq '--dry-run') {
    $dry_run = 1;
    shift @ARGV;
}
my $root = shift @ARGV or die "Usage: nfd_rename.pl [--dry-run] <folder>\n";
-d $root or die "Not a folder: $root\n";

my $failures = 0;

# finddepth visits a folder's contents before the folder itself, so renaming a
# folder never invalidates paths still to be visited
finddepth({ no_chdir => 1, wanted => \&normalize_entry }, $root);

sub normalize_entry {
    my $path = $File::Find::name;
    return if $path eq $root;

    my $dir  = $File::Find::dir;
    my $name = substr($path, length($dir) + 1);

    my $nfd_name = encode('UTF-8', NFD(decode('UTF-8', $name)));
    if ($nfd_name eq $name) {
        return;
    }

    my $new_path = "$dir/$nfd_name";
    if ($dry_run) {
        print "would rename: $path\n";
        return;
    }
    if (rename($path, $new_path)) {
        print "renamed: $new_path\n";
    } else {
        print "FAILED to rename: $path ($!)\n";
        $failures++;
    }
}

exit($failures > 0 ? 1 : 0);
