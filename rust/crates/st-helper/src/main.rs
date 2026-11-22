use clap::Parser;
use st_helper::{run, HelperCli};

fn main() -> anyhow::Result<()> {
    run(HelperCli::parse())
}
