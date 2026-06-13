## AI Trading Agent Holy Grail

The holy grail for AI trading agents is

having an agent that's able to learn

from its mistakes and make improvements

on the strategy or with the vision of

becoming more profitable. The thing is

is that most AIs that you interact with,

they are very simple. You give [music] a

prompt, they give an output. But what

we're going to do today is use an

extremely powerful AI that automatically

learns from all the engagements that you

have with it. And I want to see if I can

apply that self-learning behavior to a

trading strategy. So instead of it being

prompt outcome, instead we're going to

produce a prompt that creates a strategy

that creates an outcome it can learn

from and therefore creates a new prompt

to build into the strategy. Again, what

we have is a self-improving trading

agent. You're going to have it running

24/7. And I've even made it so you can

just simply copy and paste one single

prompt, put it into your AI, and it will

set this whole thing up for you. And the

AI itself that we're using today is

completely free. It does this self-eing

process, this self-improvement process

for free. So, right after you subscribe,

let's get into it. Now, I've been doing

this a little while. And I know that

whenever you want to create a

self-arning or a self-improving process

with Claude, for example, you have to

describe how it's supposed to improve

itself. And so, it's it can get a little

bit laborious. It's very boring, and

it's very frustrating because the AI

doesn't quite understand it. However,

there's a new tool that has come out

over the last couple of months that's

been really lingering in the background

and today I've just seen the massive use

case for it in trading and it's called

Hermes agent. You might have heard of

like OpenClaw, this fully autonomous

thing that's just took the world by

storm. Well, Hermes in the background is

being touted as even better than

OpenClaw because of this self-learning

process. But Hermes agent on its own

isn't going to do all the work. We

actually have to put some smarts into

this. And this is what I do. I architect

agents to do exactly the thing I want

them to do. And so there were four

## Four Rules for a Good Agent

criteria that I came up with with

regards to what makes a good trading

agent. And I came up with these four. So

number one, it has to be accurate.

Number two, it has to be reliable.

Number three, it needs to have a very

well-defined goal. And number four, it

needs to be self-improving. It needs to

learn from its mistakes. [music] Now,

let's first talk about the accuracy

element because when I say accurate, I

mean, is the data that's coming in

accurate in the first place? Are we able

to take that information in reliably and

consistently over time? And when we feed

that information to Hermes agent, is the

information actually accurate? Over the

last couple of weeks, I tested every

single AI that is in existence. And I

tested them on their ability to do

trading. And one of the most shocking

results from that is the inaccuracy in

the data. They're all supposed to be

pulling information from the same place,

but some AIs just don't do it properly.

And so there is inherently an accuracy

issue. So we need to make sure that that

is resolved, and we will resolve it in

this build. So when we're trying to get

accurate data through, we need to make

sure that the API connections are very

strong and reliable. We also need to

make sure that if we're pulling in

information from news feeds, for

example, that that information is also

accurate because the AI can sometimes

interpret text in different ways. If you

give the same article to multiple

different agents, they might have

different things to say and different

conclusions. So, we need to make sure

that there are rules in place so that

the conclusions are accurate and ideally

accurate and objective. And that brings

me on to the reliability section. We

need this agent to be reliable. So, what

do I mean by reliable? I would say that

reliable is it's always operating 24/7

and even if our computer goes down or

turns off or closes and it's still

executing on the system that we're going

to build today. We have also solved that

issue in the oneshot prompt. Now, it's

incredibly important that we move on to

number three, and that is that we have

## Goals Beat Predictions Every Time

to make sure that the agent has a

well-defined goal. So, let's talk about

goals a little bit deeper because it

will make all the sense in the world to

you in just a second. So, we need to

define in terms of a goal, obviously,

there is a destination. Most people, I'd

say 90% of people right now, if you're

creating a trading strategy, don't have

a destination, don't have a definition

of what achieving the goal actually

looks like. And so in light of being

thorough and actually accurate and

actually having a good agent. So we need

to define what is success and what is

failure. And it might sound a bit

abstract but in fact we need this

information. What is success in the

strategy? Is success making $10 a month?

Is success making a million dollars a

month? Obviously you have to operate in

the in the realms of what's possible. An

example of something that would be

impossible would be like saying I want

to make a million dollars a month and

here's $10 to start with. So what is

success? And the more details you can

give here, the better. Like if you know

anything about sharp scores, which is

essentially a a score that relates to

the profitability of a a trading

strategy, you would might want to put a

specific sharp score into the agent as a

goal, right? We want to work towards

this goal. Cuz if you think about it,

this agent's going to be doing a thing,

getting feedback, and then improving the

thing to do it again. And it's going to

do that over and over and over again

until that goal is achieved. And so we

need to define the goal, right? But we

also need to determine what is failure.

What does failure look like? And so what

the agent will be able to do in the end

is almost like look look at where it is

in its current results and say okay

anything in this direction is towards

the goal and this is good and anything

in the wrong direction away from the

goal closer to failure is bad. [music]

And it's with that goal in mind that

number four comes in and that is that it

needs to be self-improving. So it needs

## Scientific Method for AI Strategy

to be able to assemble and organize

information properly. It needs to learn

from the outcomes. It needs to analyze

[music] the outcomes. Were they towards

the goal or away from the goal? It then

needs to form its own hypothesis about

[music] why the result was the way it

was based on the information it had. And

then it needs to make a second

hypothesis about what it should do next.

[music]

So then it should take that information

and that learning apply it to a updated

strategy. And this updated strategy, I

think, should follow the scientific

model, which if you don't know what the

scientific method is, it's essentially

changing only one variable and then

seeing the outcome. Because if you

changed a load of variables and you went

more profitable, you wouldn't know which

variable was responsible for that trade

going well. And so you only change one

variable at a time and you run a series

of tests. Every time you get one better,

that is now the new baseline. And then

you make iterations on that new

baseline. And it needs to do this

inherently. And so all four of those

things make up what I think is a good

agent. So now's the time that we're

going to start creating this. I'm going

to demo it for you from start to finish.

The setup of this thing, the prompt,

everything is completely free for you to

use. I'm going to give you everything

## One-Shot Prompt Setup

that you need to copy and paste and get

this agent up and running with Hermes.

Okay. So, as always, every single prompt

is freely available for you to copy and

paste. And I hold them all in 01

systems. It's my own free community that

you can join right now. The link is in

the top line of this of the description.

And anytime I post any prompts in any

future videos, the links and the prompts

will all be in 01 systems. You'll come

here to start with. You'll click

classroom at the top. Then we're going

to click this big YouTube button. This

is for all the YouTube video prompts.

And you'll come across something titled

something similar to this.

Self-improving trading agent. Hermes

self-improving trading agent. The video

will also be in here cuz you can see

this is how I post it when it's live.

And we'll open that up. And we're going

to take this beautiful oneshot prompt.

It is so nice. And by the way, all of

the oneshot prompts that I give in my

videos, they improve over time as well

because we get feedback. People have

certain issues and then we improve them.

So the version that you're downloading

right now or that you get in 01 systems

will be the most up to date and the best

one we've had so far. And it's only

getting better. It's so cool. Okay. So,

what we're going to do is we're going to

come over to our terminal. And this is

me in my terminal. I'm just going to

increase the size so you can see it.

We're going to start a new session,

which I do with dangerously skip

permissions because I'm an absolute

savage. Okay. And then here we are.

We're going to we're in Claude and we're

just going to paste in our oneshot

prompt. So get ready because the journey

begins for your self-arning,

self-improving agent that's going to run

on Hermes. So as we go through this

process, we're going to do a series of

phases which you'll see on the screen

right now. So phase one was an

environment check. What this does, it's

really cool, is it makes sure uh [music]

it knows which system you're on. Are you

on a Mac or on a Windows? And depending

on which one you choose, then it will

take you on a different journey because

there's different instructions for both.

So it said, okay, we can see that you're

on a Mac and you've got no.js. JS

installed on clawed code. Great. Step

two of seven in phase two, which is

defining the strategy. We're going to

build your trading strategy now.

Specifically, what success and failures

look like. The the agent uses this file

to score every trade. It's not just

vibes, it's just numbers. So, we have to

now decide which asset are we going to

be trading. Now, this is the moment

where if you already have a strategy,

you come down to number four and you

actually you actually would say

something along the lines of this. Hey,

I've actually already got a strategy and

it's called the Wacko Alpha strategy.

So, could you look for that in my

computer and, you know, use that as part

of this system? Or alternatively, you

could say, I don't have a strategy. Can

you just make me a basic one? And it

will make you a basic one. Uh, like a

basic solid one that everyone kind of

starts with and then you can let the

agent improve it over time rather than

you. Alternatively, you can build the

strategy in this system. The onboarding

agent will work with you to create a

strategy, too. So, you could choose

Salana or USD or Ethereum or Bitcoin or

any asset. Uh, but for now, I'm just

going to say number four. I've actually

already got a strategy. It's going to

call in to my information about that

strategy that I already have, and it's

going to build out my documentation

based on that, which I think is so cool.

So, let's let this work for a little

while. We're going to go onto phase

three after it's found my Wacko Alpha

strategy. You can see it actually has

found it right here. And what's

wonderful is I've created this strategy

already and it's got over a million and

a half data points that it's analyzed.

I've been letting it run for like six to

eight weeks just learning from the

information and I did that all manually

like instructing how to learn this

stuff. But the Hermes agent will just

learn it itself. So it says what I'm

seeing on the disc got wacko alpha the

dwal momentum and yield strategy. Um how

do I how do you want me to incorporate

wacko alpha into this Hermes deploy?

Yeah, let's actually point it at this

strategy. Like, let's actually This is

real money that's being traded, by the

way. So, maybe this is maybe a bit of a

mistake, but by the way, you can see the

progress of my £50,000 to £500,000 in a

year challenge that I'm doing. I'll call

it the the 10X challenge. You can see

that the dashboard is linked below. You

can see my progress. The frustration is

for me is that I haven't been able to

put as much money in as I wanted to. I

haven't had the dips in the market that

I wanted to make my purchases. So,

that's kind of been a little bit

difficult. Okay, so now it's actually

pulled out the goals and it said the

maximum return 30 days is this much 10x

in 6 months uh 40. It's defining all my

stuff. My my minimum sharp score, my max

draw down, my failure below reflection

every certain amount of days. It's got

all of this built in. So that's

wonderful. And it says, do you want to

confirm the Hermes and Wacko Alpha

setup? Yes. So I'm going to have lock in

as proposed. That's what I'm going to go

for cuz this is actually real money. But

I want you to know that I trust this

system and the way that it learns uh

sufficiently to put my real money on the

line. That's what we're doing here. And

let's move on to the next phase. Okay.

So phase three is now scaffolding the

Hermes side state. So scaffolding all

the folders and files to be properly

analyzed by Hermes when we actually come

to install Hermes. Okay. Now we're on

phase four which has skipped actually

because the Wacko alpha is already

deployed. If you hadn't got a strategy

already, it would start to deploy that

strategy. uh to make it live. You might

have to connect in like APIs or whatever

to make it trade for you. But I've got a

video on how to actually make things

trade. It's like clawed code with

trading view that actually trades. You

can also find that in 01 systems, by the

way. The whole prompt for that is there.

So, I'm having a little bit of an issue

logging into railway, which is going to

be the place where we host this 24/7.

So, it can run regardless of whether the

computer's on or not. It's saying it

can't run interactive login from inside

this session. Please run this in the

prompt yourself. So, all I'm going to do

is going to come over here in my cursor,

split this terminal so I can start a new

terminal session. And I'm just pasting

## Trading Real Money With Hermes

in this. So, I'm going to paste that in.

It should then open up Railway for me to

get me to log in. And it's a success.

So, I can close the page. Boom. That's

done. I can now close that. And I'm now

logged in. So, I can say done,

continuing. I love these oneshot prompts

because I built them so that it opens up

these browsers for you. So, if you don't

have a Railway account, by the way, what

would have happened just then is that it

would have opened up Railway and you

just make an account, right? And then

you come back and say, "Hey, I've just

made an account." And it's free for so

much usage. Like, I've only just now

started having to pay for Railway

because I and I've cuz I've got like 50

projects on there running 24/7, right?

So, it's now using the CLI, it's called,

to integrate with Railway. So anytime

you publish a new strategy or you make a

change, it will update on the 24/7

server and just that's it. Just kind of

works like that. So that's what's great

about Railway cuz it works in this way

with your terminal. Any project that

you're doing is just kind of updating.

It's now just seen uh 24 gain trades and

22 loss trades and it's converting those

into a Hermes readable ledger. So it's

now converting everything to be

perfectly primed and ready for Hermes to

take a look at it. Uh something else has

just happened here. Oh yes, these new

documents have just uh come up. Sorry

about my desktop. Let's hide all the

clutter. So my strategy document has now

been populated. Um it's also o opening

them in my cursor as well. That's really

helpful. It's got my strategy. This is

my strategy. The maximum amount of

positions I'm going to hold is 12. My

slippage tolerance is this. My gas

reserve is this. The scorer weights

everything. This is all pulled from my

actual uh strategy. It's also defined my

goals, right? My target uh return over

30 days is 4.7 which is 47% by the way.

Um that's my target return for every 30

days. So it's going to be working until

it achieves these goals. These are all

the trades that have taken place as

well. How cool is that? So it's just

organizing all these files now so Hermes

can have a look at it and and learn from

it. So right now we're in the handoff to

Hermes phase like just like that. So I

hope you're if you're following along I

think it's about time to subscribe,

don't you? Anyway, so it's basically

looking at it's we're Oh my goodness, it

already installed Hermes. It already did

it. It already installed Hermes. That is

insane. Actually, I didn't realize that

would just happen so quickly. Okay, so

Hermes has now been installed. I can now

type in Hermes in this terminal or any

other terminal. So, let's go over to the

split terminal again. And I'm just going

to type Hermes in here just to see if it

is in fact I can't cuz I can't quite

believe that it did install. It did. And

now I fully have Hermes just up and

running. That was just so quick. We

could do a whole video on Hermes, by the

way. It's unbelievable. But what it's

done right now is it has outlined

everything that it's doing. So now that

we know Hermes is actually installed, we

can come back to that later. So let's

have a look at everything. So final

confirmation, we have a self-improving

trading agent which is deployed right

now and adapted for my Wacko Alpha

strategy which trade real money by the

way. It's working on railway 24/7 and

## Bittensor Subnet Strategy Live

its strategy is using the bit tensor

subnetss. We're looking for that return.

We're looking for that as a max draw

down minimum sharp of one. The brain is

Hermes. It's going to be watching the

live service and weekly cadence. Hermes

owns the portfolio mechanics and score

weights. And Cornelius, who's another

agent of mine, owns the filter

thresholds. The first cycle is read only

and review only. It's going to flip the

strategy

uh YAML mode to live when it's ready.

So, it's actually not trading yet. And

when it's ready, Hermes will decide it's

time to go, baby. And then we'll start

making some money. Okay. So, what

happens from here? My strategy will keep

firing every 30 minutes on railway,

which it does already. It does a daily

reshuffle and a 30 minute reshuffle.

Cornelius, my other agent, is going to

keep tuning the learned parameters JSON

every week. That means he's like looking

at all the data that comes in, those one

and a half million data points that we

have right now, and kind of learning

from those. So, I bet Cornelius and

Hermes now are just kind of together.

Hermes reviews the trades weekly, but he

has a 3-day offset from Cornelius.

Interesting. The first Hermes cycle will

produce a markdown review with no actual

writing. Um, I will approve by setting

mode. Um, so I can come here and just

like approve the strategy from there.

And a day after check-in, I can do any

of the check-ins that I want using these

commands. To go live, not today, is to

edit the Hermes trading strategy. And

Hermes will start writing on the next

weekly cycle. Hermes is watching. Close

this terminal. The agent is running.

Kablam. Kaboom. There we go. What about

that then?

So cool. Okay. So, I really want to hear

your stories about this when you've

installed. The best place to come and do

that is to come into the classroom.

There's another person that's joined

who's actually taken on the 60-day

challenge. Come into the YouTube video

prompts. Take your prompt here. come

over to the community once you've done

it and tell us all about it, how cool it

was and that that it works wonderfully

the response of the last uh video

regarding the uh Marov uh method

strategy. People have absolutely you can

see like no one has any issues with this

thing. They loved it and they're using

it and people are very excited about it.

But let's see the response of this one.

So we'll have a similar post like that

on the community when you come in.

Anyway, I want to thank you for being

here. This is so much fun, right? um

click like and if you can there's

actually a capability of you to do

something called a hype. 