use clap::Parser;
use st_server::{run, ServerArgs};

fn main() -> anyhow::Result<()> {
    run(ServerArgs::parse())
}
