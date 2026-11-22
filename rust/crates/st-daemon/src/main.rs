use clap::Parser;
use st_daemon::{run, DaemonArgs};

fn main() -> anyhow::Result<()> {
    run(DaemonArgs::parse())
}
