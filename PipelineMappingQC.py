








################################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id: PipelineGO.py 2877 2010-03-27 17:42:26Z andreas $
#
#   Copyright (C) 2009 Andreas Heger
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#################################################################################
"""
======================================================
PipelineMappingQC.py - common tasks for QC'ing mapping
======================================================

:Author: Andreas Heger
:Release: $Id$
:Date: |today|
:Tags: Python

Purpose
-------


Usage
-----

Type::

   python <script_name>.py --help

for command line help.

Code
----


"""

import Experiment as E
import logging as L
import Database, CSV

import sys, os, re, shutil, itertools, math, glob, time, gzip, collections, random

import numpy, sqlite3
import GTF, IOTools, IndexedFasta
from rpy2.robjects import r as R
import rpy2.robjects as ro
import rpy2.robjects.vectors as rovectors
import rpy2.rinterface as ri

import Pipeline as P

try:
    PARAMS = P.getParameters()
except IOError:
    pass


def buildPicardAlignmentStats( infile, outfile, genome_file ):
    '''gather BAM file alignment statistics using Picard '''

    to_cluster = True

    statement = '''CollectMultipleMetrics 
                                       INPUT=%(infile)s 
                                       REFERENCE_SEQUENCE=%(genome_file)s
                                       ASSUME_SORTED=true 
                                       OUTPUT=%(outfile)s 
                                       VALIDATION_STRINGENCY=SILENT 
                   > %(outfile)s '''

    P.run()

def buildPicardGCStats( infile, outfile, genome_file ):
    '''Gather BAM file GC bias stats using Picard '''
    to_cluster = True

    statement = '''CollectGcBiasMetrics
                                       INPUT=%(infile)s 
                                       REFERENCE_SEQUENCE=%(genome_file)s
                                       OUTPUT=%(outfile)s 
                                       VALIDATION_STRINGENCY=SILENT 
                                       CHART_OUTPUT=%(outfile)s.pdf 
                                       SUMMARY_OUTPUT=%(outfile)s.summary
                   > %(outfile)s '''

    P.run()

def loadPicardMetrics( infiles, outfile, suffix, pipeline_suffix = "alignstats" ):
    '''load picard metrics.'''

    tablename = P.toTable( outfile )
    tname = "%s_%s" % (tablename, suffix)

    outf = P.getTempFile()

    filenames = [ "%s.%s" % (x, suffix) for x in infiles ]

    first = True

    for filename in filenames:
        track = P.snip( os.path.basename(filename), ".%s.%s" % (pipeline_suffix, suffix ) )

        if not os.path.exists( filename ): 
            E.warn( "File %s missing" % filename )
            continue

        lines = IOTools.openFile( filename, "r").readlines()
        
        # extract metrics part
        rx_start = re.compile("## METRICS CLASS")
        for n, line in enumerate(lines):
            if rx_start.search(line ):
                lines = lines[n+1:]
                break

        for n, line in enumerate(lines):
            if not line.strip(): 
                lines = lines[:n]
                break
            
        if len(lines) == 0:
            E.warn("no lines in %s: %s" % (track,f))
            continue
        if first: outf.write( "%s\t%s" % ("track", lines[0] ) )
        first = False
        for i in range(1, len(lines)):
            outf.write( "%s\t%s" % (track,lines[i] ))
            
    outf.close()

    tmpfilename = outf.name

    statement = '''cat %(tmpfilename)s
                | python %(scriptsdir)s/csv2db.py
                      --index=track
                      --table=%(tname)s 
                > %(outfile)s
               '''
    P.run()

    os.unlink( tmpfilename )

def loadPicardHistogram( infiles, outfile, suffix, column, pipeline_suffix = "alignstats" ):
    '''extract a histogram from a picard output file and load it into database.'''

    tablename = P.toTable( outfile )
    tname = "%s_%s" % (tablename, suffix)
    
    tname = P.snip( tname, "_metrics") + "_histogram"

    # some files might be missing
    xfiles = [ x for x in infiles if os.path.exists( "%s.%s" % (x, suffix) ) ]
    
    header = ",".join( [P.snip( os.path.basename(x), ".%s" % pipeline_suffix) for x in xfiles ] )        
    filenames = " ".join( [ "%s.%s" % (x, suffix) for x in xfiles ] )

    statement = """python %(scriptsdir)s/combine_tables.py
                      --regex-start="## HISTOGRAM"
                      --missing=0
                   %(filenames)s
                | python %(scriptsdir)s/csv2db.py
                      --header=%(column)s,%(header)s
                      --replace-header
                      --index=track
                      --table=%(tname)s 
                >> %(outfile)s
                """
    
    P.run()

def loadPicardAlignmentStats( infiles, outfile ):
    '''load all output from Picard's CollectMultipleMetrics
    into sql database.'''

    loadPicardMetrics( infiles, outfile, "alignment_summary_metrics" )
    loadPicardMetrics( infiles, outfile, "insert_size_metrics" )

    for suffix, column in ( ("quality_by_cycle_metrics", "cycle"),
                            ("quality_distribution_metrics", "quality"),
                            ("insert_size_metrics", "insert_size" ) ):

        loadPicardHistogram( infiles, outfile, suffix, column )

def loadPicardDuplicateStats( infiles, outfile ):
    '''load picard duplicate filtering stats.'''

    loadPicardMetrics( infiles, outfile, "duplicate_metrics", "bam" )
    loadPicardHistogram( infiles, outfile, "duplicate_metrics", "duplicates", "bam" )
    
def buildBAMStats( infile, outfile ):
    '''Count number of reads mapped, duplicates, etc. '''
    to_cluster = True

    statement = '''python %(scriptsdir)s/bam2stats.py 
                          --force 
                          --output-filename-pattern=%(outfile)s.%%s 
                          < %(infile)s 
                          > %(outfile)s'''
    P.run()


def loadBAMStats( infiles, outfile ):
    '''load bam2stats.py output into sqlite database.'''

    # scriptsdir = PARAMS["general_scriptsdir"]
    header = ",".join( [P.snip( os.path.basename(x), ".readstats") for x in infiles] )
    filenames = " ".join( [ "<( cut -f 1,2 < %s)" % x for x in infiles ] )
    tablename = P.toTable( outfile )
    E.info( "loading bam stats - summary" )
    statement = """python %(scriptsdir)s/combine_tables.py
                      --headers=%(header)s
                      --missing=0
                      --ignore-empty
                   %(filenames)s
                | perl -p -e "s/bin/track/"
                | perl -p -e "s/unique/unique_alignments/"
                | python %(scriptsdir)s/table2table.py --transpose
                | python %(scriptsdir)s/csv2db.py
                      --allow-empty
                      --index=track
                      --table=%(tablename)s 
                > %(outfile)s"""
    P.run()

    for suffix in ("nm", "nh"):
        E.info( "loading bam stats - %s" % suffix )
        filenames = " ".join( [ "%s.%s" % (x, suffix) for x in infiles ] )
        tname = "%s_%s" % (tablename, suffix)
        
        statement = """python %(scriptsdir)s/combine_tables.py
                      --header=%(header)s
                      --skip-titles
                      --missing=0
                      --ignore-empty
                   %(filenames)s
                | perl -p -e "s/bin/%(suffix)s/"
                | python %(scriptsdir)s/csv2db.py
                      --table=%(tname)s 
                      --allow-empty
                >> %(outfile)s """
        P.run()

