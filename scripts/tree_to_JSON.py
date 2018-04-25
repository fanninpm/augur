from __future__ import division, print_function
from Bio import Phylo
from StringIO import StringIO
import json
import numpy as np
import argparse
import sys
import imp
import logging
sys.path.append('')
from base.colorLogging import ColorizingStreamHandler


version = 0.1

def get_command_line_args():
    parser = argparse.ArgumentParser(description ="Convert tree files to Auspice ready JSONs. Version {}".format(version))

    newick = parser.add_argument_group('newick')
    newick.add_argument('--newick', type=str, help="Path to newick file")

    beast = parser.add_argument_group('beast')
    beast.add_argument('--nexus', type=str, help="Path to nexus file")
    beast.add_argument('--most_recent_tip', type=float, help="Date of the most recent tip (in decimal format)")
    beast.add_argument('--discrete_traits', type=str, nargs='+', default=[], help="Discrete traits to extract from the BEAST annotations")
    beast.add_argument('--continuous_traits', type=str, nargs='+', default=[], help="Continuous traits to extract from the BEAST annotations")

    metadata = parser.add_argument_group('metadata')


    general = parser.add_argument_group('general')
    general.add_argument("--maintainer", type=str, nargs=2, default=["", ""], help="Maintaner (display name, link name). Shown in auspice footer.")
    general.add_argument("--debug", action="store_const", dest="loglevel", const=logging.DEBUG, help="Enable debugging logging")
    general.add_argument('--output_prefix', '-o', required=True, type=str, help="Output prefix (i.e. \"_meta.json\" will be appended to this)")
    general.add_argument('--title', default=None, type=str, help="Title (to be displayed by auspice)")

    return parser.parse_args()

# Biopython's trees don't store links to node parents, so we need to build
# a map of each node to its parent.
# Code from the Bio.Phylo cookbook: http://biopython.org/wiki/Phylo_cookbook
def all_parents(tree):
    parents = {}
    for clade in tree.find_clades(order='level'):
        for child in clade:
            parents[child] = clade
    return parents

def annotate_phylotree_parents(tree):
    # Get all parent nodes by node.
    parents_by_node = all_parents(tree)

    # Next, annotate each node with its parent.
    for node in tree.find_clades():
        if node == tree.root:
            node.parent = None
        else:
            node.parent = parents_by_node[node]

    # Return the tree.
    return tree


def modified_tree_to_json(node, extra_attr = []):
    tree_json = {}
    str_attr = ['strain','attr']
    num_attr = ['yvalue', 'tvalue', 'num_date', 'clade']
    if hasattr(node, 'name'):
        tree_json['strain'] = node.name

    for prop in str_attr:
        if hasattr(node, prop):
            tree_json[prop] = node.__getattribute__(prop)
    for prop in num_attr:
        if hasattr(node, prop):
            try:
                tree_json[prop] = round(node.__getattribute__(prop),5)
            except:
                print("cannot round:", node.__getattribute__(prop), "assigned as is")
                tree_json[prop] = node.__getattribute__(prop)

    for prop in extra_attr:
        if len(prop)==2 and callable(prop[1]):
            if hasattr(node, prop[0]):
                tree_json[prop] = prop[1](node.__getattribute__(prop[0]))
        else:
            if hasattr(node, prop):
                tree_json[prop] = node.__getattribute__(prop)

    if node.clades:
        tree_json["children"] = []
        for ch in node.clades:
            tree_json["children"].append(modified_tree_to_json(ch, extra_attr))
    return tree_json

def mock_meta_json(tree, args):
    meta = {}
    meta["updated"] = "today"
    meta["virus_count"] = tree.virus_count
    meta["maintainer"] = args.maintainer

    if not args.title:
        meta["title"] = args.newick
    else:
        meta["title"] = args.title

    meta["color_options"] = {
      "country": {
        "menuItem": "country",
        "type": "discrete",
        "legendTitle": "country",
        "key": "country"
      }
    }
    meta["filters"] = ["type"]
    meta["commit"] = "unknown"
    meta["panels"] = ["tree"]
    meta["geo"] = {"country": {}}
    meta["annotations"] = {}
    meta["author_info"] = {}
    return meta;

def set_basic_information_on_nodes(tree):
    count = 0;
    for node in tree.find_clades():
        count += 1
        if not node.name:
            node.name = "CLADE_{}".format(count)
        setattr(node,'attr', {})
        setattr(node,'clade', count)
        setattr(node,'strain', node.name)

def set_y_values(tree):
    count = 0;
    for node in tree.find_clades():
        if node.is_terminal():
            setattr(node, 'yvalue', count)
            count += 1
    # set internal y-values
    for node in tree.get_nonterminals(order="postorder"):
        setattr(node, 'yvalue', np.mean([x.yvalue for x in node.clades]))
    tree.virus_count = count

def set_divergence_on_tree(tree):
    for node in tree.find_clades():
        if node.name == "root":
            node.branch_length = 0.0 # root node
            node.cumulative_length = 0.0
        else:
            node.cumulative_length = node.parent.cumulative_length + node.branch_length
        node.attr["div"] = node.cumulative_length

def make_up_temporal_data(tree):

    fake_date_range = [2000, 2018]
    max_divergence = 0;
    for node in tree.find_clades():
        if node.attr["div"] > max_divergence:
            max_divergence = node.attr["div"]

    for node in tree.find_clades():
        node.attr["num_date"] = fake_date_range[0] + (node.attr["div"] / max_divergence) * (fake_date_range[1] - fake_date_range[0])


def make_up_country(tree):
    countries = ["USA", "New Zealand", "France", "Kenya"]
    for node in tree.find_clades():
        if node.is_terminal():
            node.attr["country"] = np.random.choice(countries)


if __name__=="__main__":
    args = get_command_line_args()
    root_logger = logging.getLogger('')
    root_logger.setLevel(args.loglevel if args.loglevel else logging.INFO)
    root_logger.addHandler(ColorizingStreamHandler())
    logger = logging.getLogger(__name__)


    ## PARSE THE TREE ##
    if args.newick:
        logger.info("Loading newick tree {}".format(args.newick))
        tree = Phylo.read(args.newick, "newick");
        tree.ladderize()
        tree = annotate_phylotree_parents(tree)
        set_basic_information_on_nodes(tree)
        set_y_values(tree)
        set_divergence_on_tree(tree)
        make_up_temporal_data(tree)
        make_up_country(tree)
    elif args.nexus:
        try:
            bt = imp.load_source('baltic', '/Users/james/blab/baltic/baltic.py')
        except IOError:
            bt = imp.load_source('baltic', '/Users/evogytis/Documents/BLAB_baltic/baltic.py')
        logger.info("Loading nexus tree {}".format(args.nexus))
        bt_tree = bt.loadNexus(args.nexus, absoluteTime=False) ## loads a BEAST nexus file
        bt_tree.setAbsoluteTime(args.most_recent_tip)

        tree = Phylo.read(StringIO(bt_tree.toString()), 'newick')
        tree = annotate_phylotree_parents(tree)
        nodes=tree.get_nonterminals(order='preorder') ## fetch nodes from newick tree
        bt_nodes=[k for k in bt_tree.traverse_tree(include_all=True) if k.branchType=='node'] ## fetch nodes from baltic tree
        leaves=tree.get_terminals(order='preorder') ## fetch leaves from newick tree
        bt_leaves=[k for k in bt_tree.traverse_tree()] ## fetch leaves from baltic tree
        tree.virus_count = len(leaves)

        # iterate over BT (nexus/BEAST) nodes & leaves as we iterate over the biophylo tree
        # and transfer relevent data
        for pairs in [[nodes,bt_nodes],[leaves,bt_leaves]]:
            for node, b in zip(*pairs):
                setattr(node,'tvalue',b.height) ## height in years
                setattr(node,'xvalue',b.height) ## 0.0 in the future?
                setattr(node,'yvalue',b.y) ## y value after drawing
                setattr(node,'clade',b.index) ## clade
                attrs={}

                if b.absoluteTime!=None:
                    attrs['num_date']=b.absoluteTime
                else: # root
                    attrs['num_date']=bt_tree.Objects[0].absoluteTime

                setattr(node,'attr',attrs)

                for trait in args.discrete_traits:
                    if b.traits.has_key(trait):
                        attrs[trait] = b.traits[trait]
                        tset = trait+".set"
                        tprob = trait+".set.prob"
                        if b.traits.has_key(tset) and b.traits.has_key(tprob):
                            attrs[trait+'_confidence']={t:p for t,p in zip(b.traits[tset], b.traits[tprob])}

                for trait in args.continuous_traits:
                    rate = trait+".rate"
                    hpd = trait+".rate_95%_HPD"
                    if b.traits.has_key(rate):
                        attrs[trait] = b.traits[rate]
                        if b.traits.has_key(hpd):
                            attrs[trait+'_confidence'] = b.traits[hpd]


        # import pdb; pdb.set_trace()



    tree_json = modified_tree_to_json(tree.root)
    json.dump(tree_json, open("{}_tree.json".format(args.output_prefix), 'w'), indent=2)
    meta_json = mock_meta_json(tree, args)

    ## add the discrete & continous traits as colorBys
    for trait in args.discrete_traits:
        meta_json["color_options"][trait] = {"menuItem": trait, "type": "discrete", "legendTitle": trait, "key": trait}
    for trait in args.continuous_traits:
        meta_json["color_options"][trait] = {"menuItem": trait, "type": "continous", "legendTitle": trait, "key": trait}


    json.dump(meta_json, open("{}_meta.json".format(args.output_prefix), 'w'), indent=2)

    logger.info("DONE")
